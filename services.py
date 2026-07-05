import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
from huggingface_hub import HfApi
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class MemoryManager:
    def __init__(self, hf_token: str, dataset_repo: str):
        self.api = HfApi(token=hf_token)
        self.dataset_repo = dataset_repo
        self.data = {}
        self.dirty = False
        self.last_sync = 0.0
        self.lock = asyncio.Lock()
        self._bg_task = None

    async def load(self):
        try:
            path = await asyncio.to_thread(
                self.api.hf_hub_download,
                repo_id=self.dataset_repo,
                filename="bot_data.json",
                local_dir="/tmp",
                token=self.api.token,
            )
            with open(path) as f:
                self.data = json.load(f)
            logger.info("Dataset loaded: %d conversations", len(self.data.get("conversations", {})))
        except Exception as e:
            logger.warning("Dataset load failed, fresh start: %s", e)
            self.data = {
                "conversations": {},
                "notes": [],
                "costs": {
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "daily_date": "",
                    "daily_input_tokens": 0,
                    "daily_output_tokens": 0,
                },
                "user_facts": {},
            }

    async def _save_now(self):
        path = "/tmp/bot_data.json"
        with open(path, "w") as f:
            json.dump(self.data, f, ensure_ascii=False)
        await asyncio.to_thread(
            self.api.upload_file,
            repo_id=self.dataset_repo,
            path_or_fileobj=path,
            path_in_repo="bot_data.json",
            token=self.api.token,
        )
        self.last_sync = asyncio.get_event_loop().time()
        self.dirty = False
        logger.info("Dataset saved")

    async def save(self, force=False):
        if not self.dirty:
            return
        now = asyncio.get_event_loop().time()
        if force or now - self.last_sync > 30:
            await self._save_now()

    async def _bg_sync(self):
        while True:
            await asyncio.sleep(30)
            await self.save()

    def start(self):
        loop = asyncio.get_event_loop()
        self._bg_task = loop.create_task(self._bg_sync())

    async def stop(self):
        if self._bg_task:
            self._bg_task.cancel()
        await self.save(force=True)

    async def get_user_history(self, user_id: str) -> list:
        return self.data.setdefault("conversations", {}).get(user_id, [])

    async def add_message(self, user_id: str, role: str, content: str):
        async with self.lock:
            hist = self.data.setdefault("conversations", {}).setdefault(user_id, [])
            hist.append({"role": role, "content": content[:2000]})
            if len(hist) > 20:
                hist.pop(0)
            self.dirty = True

    async def clear_history(self, user_id: str):
        async with self.lock:
            self.data.setdefault("conversations", {})[user_id] = []
            self.dirty = True

    async def get_user_facts(self, user_id: str) -> list:
        return self.data.setdefault("user_facts", {}).get(user_id, [])

    async def add_facts(self, user_id: str, facts: list):
        if not facts:
            return
        async with self.lock:
            exist = set(f.lower() for f in self.data.setdefault("user_facts", {}).setdefault(user_id, []))
            for f in facts:
                if f.lower() not in exist:
                    self.data["user_facts"][user_id].append(f)
                    exist.add(f.lower())
            self.dirty = True

    async def clear_facts(self, user_id: str):
        async with self.lock:
            self.data.setdefault("user_facts", {})[user_id] = []
            self.dirty = True

    async def get_notes(self) -> list:
        return self.data.get("notes", [])[-10:]

    async def add_note(self, text: str):
        async with self.lock:
            self.data.setdefault("notes", []).append(
                {"text": text, "timestamp": datetime.now().isoformat()}
            )
            self.dirty = True

    async def clear_notes(self):
        async with self.lock:
            self.data["notes"] = []
            self.dirty = True

    async def log_costs(self, inp: int, out: int):
        async with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            c = self.data.setdefault("costs", {})
            c["total_input_tokens"] = c.get("total_input_tokens", 0) + inp
            c["total_output_tokens"] = c.get("total_output_tokens", 0) + out
            if c.get("daily_date") != today:
                c["daily_date"] = today
                c["daily_input_tokens"] = 0
                c["daily_output_tokens"] = 0
            c["daily_input_tokens"] = c.get("daily_input_tokens", 0) + inp
            c["daily_output_tokens"] = c.get("daily_output_tokens", 0) + out
            self.dirty = True

    async def get_costs(self) -> dict:
        return self.data.get("costs", {})


MODEL_CHAIN = ["openrouter/free", "openrouter/free", "openrouter/free"]
OR_URL = "https://openrouter.ai/api/v1/chat/completions"


async def call_openrouter(messages: list, api_key: str, tools: list = None) -> tuple[str, dict]:
    last_err = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as cl:
                body = {"model": MODEL_CHAIN[0], "messages": messages}
                if tools:
                    body["tools"] = tools
                r = await cl.post(
                    OR_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                ch = data["choices"][0]["message"]["content"]
                u = data.get("usage", {})
                return ch, {
                    "prompt_tokens": u.get("prompt_tokens", 0),
                    "completion_tokens": u.get("completion_tokens", 0),
                }
        except Exception as e:
            last_err = e
            await asyncio.sleep(2**attempt)
    raise Exception(f"OpenRouter failed: {last_err}")


FACT_PROMPT = """Проанализируй сообщение пользователя и выдели долгосрочные факты о нём: имя, профессия, место жительства, проекты, постоянные предпочтения, важные отношения, повторяющиеся привычки.
Не выделяй: разовые события, эмоции, временные состояния, вопросы к боту, команды.
Ответь СТРОГО в формате JSON-массива строк, без пояснений и markdown. Каждый элемент - один факт, кратко, от третьего лица. Если фактов нет - верни [].
Сообщение пользователя:
{text}"""


async def extract_facts(text: str, api_key: str) -> list:
    try:
        prompt = FACT_PROMPT.format(text=text[:1000])
        result, _ = await call_openrouter([{"role": "user", "content": prompt}], api_key)
        raw = result.strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        facts = json.loads(raw)
        return [str(f).strip() for f in facts if str(f).strip()] if isinstance(facts, list) else []
    except Exception as e:
        logger.error("Fact extraction error: %s", e)
        return []


async def transcribe_audio(audio_bytes: bytes, api_key: str) -> str:
    try:
        client = genai.Client(api_key=api_key)
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=[
                "Распознай речь в этом аудио. Верни только текст без пояснений.",
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
            ],
        )
        return (resp.text or "").strip()
    except Exception as e:
        logger.error("Gemini STT error: %s", e)
        raise


tavily_remaining = 1000


async def search_web(query: str, api_key: str) -> list[dict]:
    global tavily_remaining
    if tavily_remaining < 50:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query[:500], "max_results": 5},
            )
            r.raise_for_status()
            data = r.json()
            if "X-RateLimit-Remaining" in r.headers:
                tavily_remaining = int(r.headers["X-RateLimit-Remaining"])
            return data.get("results", [])
    except Exception as e:
        logger.error("Tavily error: %s", e)
        return []


async def search_x_trends(bearer: str) -> list[dict]:
    if not bearer:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(
                "https://api.x.com/2/tweets/search/recent",
                headers={"Authorization": f"Bearer {bearer}"},
                params={
                    "query": "AI OR tech OR artificial intelligence -is:retweet lang:en",
                    "max_results": 10,
                    "tweet.fields": "created_at,public_metrics",
                    "sort_order": "recency",
                },
            )
            r.raise_for_status()
            return r.json().get("data", [])
    except Exception as e:
        logger.error("X search error: %s", e)
        return []


SUBREDDITS = ["artificial", "MachineLearning", "singularity", "technology"]


async def get_reddit_trends() -> list[dict]:
    results = []
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            for sub in SUBREDDITS:
                try:
                    r = await cl.get(
                        f"https://www.reddit.com/r/{sub}/hot.json?limit=5",
                        headers={"User-Agent": "TelegramAIBot/1.0"},
                    )
                    if r.status_code != 200:
                        continue
                    for post in r.json().get("data", {}).get("children", []):
                        d = post["data"]
                        results.append({
                            "title": d.get("title", ""),
                            "url": d.get("url", ""),
                            "score": d.get("score", 0),
                            "subreddit": sub,
                        })
                except Exception:
                    continue
    except Exception as e:
        logger.error("Reddit error: %s", e)
        return []
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:10]


async def send_telegram(chat_id: int, text: str, token: str, parse_mode: str = "MarkdownV2"):
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            body = {"chat_id": chat_id, "text": text[:4000], "parse_mode": parse_mode}
            r = await cl.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
            if r.status_code == 400:
                body.pop("parse_mode")
                await cl.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
    except Exception as e:
        logger.error("Telegram send error: %s", e)


async def send_long_message(chat_id: int, text: str, token: str):
    for i in range(0, len(text), 4000):
        await send_telegram(chat_id, text[i:i + 4000], token, parse_mode=None)
