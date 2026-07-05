import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from services import (
    MemoryManager,
    call_openrouter,
    extract_facts,
    transcribe_audio,
    search_web,
    search_x_trends,
    get_reddit_trends,
    send_telegram,
    send_long_message,
    logger,
)

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
SYSTEM_PROMPT_PATH = os.path.join(PROMPTS_DIR, "system.txt")
with open(SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
    BASE_SYSTEM_PROMPT = f.read()

memory = MemoryManager(
    hf_token=settings.hf_token.get_secret_value(),
    dataset_repo=settings.dataset_repo,
)

processed_updates = set()
user_last_msg: dict[int, float] = {}
RATE_LIMIT = 1.5

telegram_token = settings.telegram_bot_token.get_secret_value()
openrouter_key = settings.openrouter_api_key.get_secret_value()
webhook_secret = settings.telegram_webhook_secret.get_secret_value()


def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last = user_last_msg.get(user_id, 0.0)
    if now - last < RATE_LIMIT:
        return True
    user_last_msg[user_id] = now
    return False


async def notify_admin(text: str):
    await send_telegram(settings.allowed_user_id, text, telegram_token, parse_mode=None)


def build_system_prompt(user_facts: list[str]) -> str:
    facts_text = "\n".join(f"- {f}" for f in user_facts) if user_facts else "Пока ничего не известно."
    return BASE_SYSTEM_PROMPT.replace("{user_facts}", facts_text)


def build_messages(sys_prompt: str, history: list, user_text: str, search_results: list[dict] = None) -> list:
    msgs = [{"role": "system", "content": sys_prompt}]
    for msg in history:
        msgs.append(msg)
    if search_results:
        ctx = "\n\n".join(
            f"{r.get('title', '')}\n{r.get('content', '')}\n{r.get('url', '')}"
            for r in search_results
        )
        user_text = f"{user_text}\n\n[Результаты поиска:]\n{ctx}"
    msgs.append({"role": "user", "content": user_text})
    return msgs


BRIEFING_PROMPT = """Ты — аналитик трендов AI/tech. Проанализируй данные из трёх источников и составь краткий отчёт.

X посты: {x_data}
Reddit посты: {reddit_data}
Главные новости: {news_data}

Формат ответа:
📰 ГЛАВНАЯ НОВОСТЬ:
[1-2 предложения о главной новости]

🔥 ТРЕНДЫ (X + Reddit):
• [тема, которая встречается в обоих источниках]
• [если есть ещё одна]

📱 X:
• [тема из X]
• [тема из X]

🔴 Reddit:
• [тема из Reddit]
• [тема из Reddit]

Детальнее: [ссылка на самый значимый пост/новость]

Правила:
- Коротко, по делу, без воды.
- Если тема встречается и в X и в Reddit — помести её в 🔥 ТРЕНДЫ.
- Если источников недостаточно — напиши что есть, не выдумывай."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    await memory.load()
    memory.start()

    async with httpx.AsyncClient() as cl:
        await cl.post(
            f"https://api.telegram.org/bot{telegram_token}/setWebhook",
            json={
                "url": f"https://{settings.space_url}/webhook",
                "secret_token": webhook_secret,
            },
        )
    logger.info("Webhook registered: https://%s/webhook", settings.space_url)

    yield

    await memory.stop()
    logger.info("Shutdown complete")


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def verify_webhook(request: Request, call_next):
    if request.url.path == "/webhook":
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if token != webhook_secret:
            return Response(status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"status": "ok", "users": len(memory.data.get("conversations", {}))}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update_id = data.get("update_id")

    if update_id in processed_updates:
        return {"ok": True, "dup": True}
    processed_updates.add(update_id)
    if len(processed_updates) > 1000:
        processed_updates.clear()

    msg = data.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    user_id = msg.get("from", {}).get("id")

    if not chat_id or not user_id:
        return {"ok": False}

    if user_id != settings.allowed_user_id:
        await send_telegram(chat_id, "Доступ закрыт.", telegram_token)
        return {"ok": True}

    if is_rate_limited(user_id):
        await send_telegram(chat_id, "Шнеку нужно передохнуть. Подожди немного.", telegram_token)
        return {"ok": True}

    text = msg.get("text", "")
    entities = msg.get("entities", [])

    if entities and entities[0].get("type") == "bot_command":
        return await handle_command(chat_id, user_id, text)

    try:
        if "photo" in msg:
            await handle_image(chat_id, user_id, msg["photo"][-1])
        elif "voice" in msg:
            await handle_voice(chat_id, user_id, msg["voice"]["file_id"])
        else:
            await handle_text(chat_id, user_id, text)
    except Exception as e:
        logger.error("Handler error: %s", e)
        await send_telegram(chat_id, "Произошла ошибка. Попробуй позже.", telegram_token)

    return {"ok": True}


async def handle_command(chat_id: int, user_id: int, text: str):
    cmd = text.split()[0].lower()

    if cmd == "/start":
        await send_telegram(
            chat_id,
            "🤖 Привет! Я AI-бот.\n\n"
            "• Отвечаю на вопросы\n"
            "• Понимаю картинки и голосовые\n"
            "• Ищу в интернете\n"
            "• Помню контекст\n\n"
            "Команды: /clear, /notes, /clearnotes, /costs",
            telegram_token,
        )

    elif cmd == "/clear":
        await memory.clear_history(str(user_id))
        await send_telegram(chat_id, "История диалога очищена.", telegram_token)

    elif cmd == "/notes":
        notes = await memory.get_notes()
        if not notes:
            await send_telegram(chat_id, "Заметок пока нет.", telegram_token)
        else:
            text = "📝 Последние заметки:\n\n" + "\n".join(
                f"• {n['text']}" for n in notes
            )
            await send_telegram(chat_id, text[:4000], telegram_token)

    elif cmd == "/clearnotes":
        await memory.clear_notes()
        await send_telegram(chat_id, "Все заметки удалены.", telegram_token)

    elif cmd == "/costs":
        costs = await memory.get_costs()
        input_cost = (costs.get("total_input_tokens", 0) / 1_000_000) * 3
        output_cost = (costs.get("total_output_tokens", 0) / 1_000_000) * 15
        total = input_cost + output_cost
        text = (
            f"📊 Статистика:\n\n"
            f"Сегодня: {costs.get('daily_input_tokens', 0)} in / {costs.get('daily_output_tokens', 0)} out токенов\n"
            f"Всего: {costs.get('total_input_tokens', 0)} in / {costs.get('total_output_tokens', 0)} out токенов\n"
            f"Примерная стоимость: ${total:.4f}"
        )
        await send_telegram(chat_id, text, telegram_token)

    return {"ok": True}


async def handle_text(chat_id: int, user_id: int, text: str):
    await send_telegram(chat_id, "🤔 Думаю...", telegram_token, parse_mode=None)

    history = []
    user_facts = []
    search_results = []

    try:
        history = await memory.get_user_history(str(user_id))
        user_facts = await memory.get_user_facts(str(user_id))
    except Exception:
        pass

    search_keywords = {"найди", "поищи", "что такое", "кто такой", "новости",
                       "цена", "курс", "погода", "когда", "где", "сколько стоит"}
    needs_search = bool(search_keywords & set(text.lower().split()))

    if needs_search:
        try:
            search_results = await search_web(text, settings.tavily_api_key.get_secret_value())
        except Exception:
            pass

    sys_prompt = build_system_prompt(user_facts)
    messages = build_messages(sys_prompt, history, text, search_results)

    try:
        answer, usage = await call_openrouter(messages, openrouter_key)
        await memory.add_message(str(user_id), "user", text)
        await memory.add_message(str(user_id), "assistant", answer)
        if usage:
            await memory.log_costs(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        await send_long_message(chat_id, answer, telegram_token)

        asyncio.create_task(extract_and_save_facts(text, user_id))
    except Exception as e:
        logger.error("OpenRouter error: %s", e)
        await send_telegram(chat_id, "Бот временно недоступен. Попробуй позже.", telegram_token)
        await notify_admin(f"OpenRouter error: {e}")


async def handle_image(chat_id: int, user_id: int, photo: dict):
    await send_telegram(chat_id, "🖼 Анализирую изображение...", telegram_token, parse_mode=None)

    try:
        file_id = photo["file_id"]
        async with httpx.AsyncClient() as cl:
            r = await cl.get(
                f"https://api.telegram.org/bot{telegram_token}/getFile",
                params={"file_id": file_id},
            )
            r.raise_for_status()
            file_path = r.json()["result"]["file_path"]

            img_r = await cl.get(
                f"https://api.telegram.org/file/bot{telegram_token}/{file_path}"
            )
            img_r.raise_for_status()
            import base64
            b64 = base64.b64encode(img_r.content).decode()

        history = []
        user_facts = []
        try:
            history = await memory.get_user_history(str(user_id))
            user_facts = await memory.get_user_facts(str(user_id))
        except Exception:
            pass

        sys_prompt = build_system_prompt(user_facts)
        msgs = [{"role": "system", "content": sys_prompt}]
        for m in history:
            msgs.append(m)
        msgs.append({
            "role": "user",
            "content": [
                {"type": "text", "text": "Что на этом изображении?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        })

        answer, usage = await call_openrouter(msgs, openrouter_key)
        await memory.add_message(str(user_id), "user", "[фото]")
        await memory.add_message(str(user_id), "assistant", answer)
        if usage:
            await memory.log_costs(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        await send_long_message(chat_id, answer, telegram_token)
    except Exception as e:
        logger.error("Image error: %s", e)
        await send_telegram(chat_id, "Не удалось обработать изображение.", telegram_token)


async def handle_voice(chat_id: int, user_id: int, file_id: str):
    await send_telegram(chat_id, "🎤 Распознаю голосовое...", telegram_token, parse_mode=None)

    try:
        async with httpx.AsyncClient() as cl:
            r = await cl.get(
                f"https://api.telegram.org/bot{telegram_token}/getFile",
                params={"file_id": file_id},
            )
            r.raise_for_status()
            file_path = r.json()["result"]["file_path"]
            audio_r = await cl.get(
                f"https://api.telegram.org/file/bot{telegram_token}/{file_path}"
            )
            audio_r.raise_for_status()
            audio_bytes = audio_r.content

        gemini_key = settings.google_genai_api_key.get_secret_value() if settings.google_genai_api_key else ""
        if not gemini_key:
            await send_telegram(chat_id, "Голосовые сообщения не поддерживаются (не настроен Gemini).", telegram_token)
            return

        transcript = await transcribe_audio(audio_bytes, gemini_key)
        if not transcript:
            await send_telegram(chat_id, "Не удалось распознать речь.", telegram_token)
            return

        await send_telegram(chat_id, f"📝 Распознано: {transcript[:200]}", telegram_token, parse_mode=None)
        await handle_text(chat_id, user_id, transcript)
    except Exception as e:
        logger.error("Voice error: %s", e)
        await send_telegram(chat_id, "Не удалось обработать голосовое.", telegram_token)


async def extract_and_save_facts(text: str, user_id: int):
    try:
        facts = await extract_facts(text, openrouter_key)
        if facts:
            await memory.add_facts(str(user_id), facts)
    except Exception as e:
        logger.error("Fact extraction error: %s", e)


@app.post("/daily-briefing")
async def daily_briefing(request: Request):
    secret = request.headers.get("Authorization")
    if secret and secret != f"Bearer {webhook_secret}":
        return Response(status_code=403)

    logger.info("Generating daily briefing...")

    x_data, reddit_data, news_data = [], [], []

    try:
        if settings.x_bearer_token:
            x_data = await search_x_trends(settings.x_bearer_token.get_secret_value())
    except Exception as e:
        logger.error("Briefing X error: %s", e)

    try:
        reddit_data = await get_reddit_trends()
    except Exception as e:
        logger.error("Briefing Reddit error: %s", e)

    try:
        if settings.tavily_api_key:
            news_results = await search_web("главная новость AI tech за сегодня", settings.tavily_api_key.get_secret_value())
            news_data = news_results[:3]
    except Exception as e:
        logger.error("Briefing Tavily error: %s", e)

    prompt = BRIEFING_PROMPT.format(
        x_data=json.dumps(x_data, ensure_ascii=False)[:2000],
        reddit_data=json.dumps(reddit_data, ensure_ascii=False)[:2000],
        news_data=json.dumps(news_data, ensure_ascii=False)[:1000],
    )

    try:
        answer, usage = await call_openrouter(
            [{"role": "system", "content": "Ты — аналитик трендов AI/tech. Отвечаешь кратко, по делу, на русском."},
             {"role": "user", "content": prompt}],
            openrouter_key,
        )
        briefing = f"Доброе утро ☀️\n\n{answer}"
        await send_telegram(settings.allowed_user_id, briefing, telegram_token, parse_mode=None)
        if usage:
            await memory.log_costs(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        logger.info("Briefing sent")
    except Exception as e:
        logger.error("Briefing generation error: %s", e)
        await send_telegram(settings.allowed_user_id, "Не удалось сгенерировать брифинг.", telegram_token)

    return {"ok": True}
