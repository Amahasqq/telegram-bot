import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from huggingface_hub import HfApi

from app.config import settings
from app.constants import (
    HF_SYNC_INTERVAL,
    MAX_HISTORY,
    MAX_TEXT_LENGTH,
    MAX_PROCESSED_UPDATES,
    PROCESSED_UPDATES_TTL_HOURS,
)

logger = logging.getLogger(__name__)

DEFAULT_COSTS: dict[str, Any] = {
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "daily_date": "",
    "daily_input_tokens": 0,
    "daily_output_tokens": 0,
}


class MemoryManager:
    def __init__(self, hf_token: str, dataset_repo: str) -> None:
        self.api = HfApi(token=hf_token)
        self.dataset_repo = dataset_repo
        self.data: dict[str, Any] = {}
        self.dirty = False
        self.last_sync = 0.0
        self.lock = asyncio.Lock()
        self._bg_task: asyncio.Task[None] | None = None

    async def load(self) -> None:
        try:
            path = await asyncio.to_thread(
                self.api.hf_hub_download,
                repo_id=self.dataset_repo,
                filename="bot_data.json",
                local_dir="/tmp",
                token=self.api.token,
            )
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            logger.info("Dataset loaded: %d conversations", len(raw.get("conversations", {})))
        except Exception as e:
            logger.warning("Dataset load failed, starting fresh: %s", e)
            raw = {}

        self.data = {
            "conversations": raw.get("conversations", {}),
            "notes": raw.get("notes", []),
            "costs": raw.get("costs", dict(DEFAULT_COSTS)),
            "user_facts": raw.get("user_facts", {}),
            "rate_limits": raw.get("rate_limits", {}),
            "processed_updates": raw.get("processed_updates", []),
            "user_chat_ids": raw.get("user_chat_ids", {}),
        }

    async def _save_now(self) -> None:
        path = "/tmp/bot_data.json"
        with open(path, "w", encoding="utf-8") as f:
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

    async def save(self, force: bool = False) -> None:
        async with self.lock:
            if not self.dirty and not force:
                return
            now = asyncio.get_event_loop().time()
            if force or now - self.last_sync > HF_SYNC_INTERVAL:
                await self._save_now()

    async def _bg_sync(self) -> None:
        while True:
            await asyncio.sleep(HF_SYNC_INTERVAL)
            await self.save()

    def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._bg_task = loop.create_task(self._bg_sync())

    async def stop(self) -> None:
        if self._bg_task:
            self._bg_task.cancel()
        await asyncio.wait_for(self.save(force=True), timeout=10.0)

    # User chat_id mapping
    async def set_user_chat_id(self, user_id: str, chat_id: int) -> None:
        async with self.lock:
            self.data.setdefault("user_chat_ids", {})[user_id] = chat_id
            self.dirty = True

    def get_user_chat_id(self, user_id: str) -> int | None:
        return self.data.get("user_chat_ids", {}).get(user_id)

    # Rate limits
    async def set_rate_limit(self, user_id: str, timestamp: float) -> None:
        async with self.lock:
            self.data.setdefault("rate_limits", {})[user_id] = timestamp
            self.dirty = True

    def get_rate_limit(self, user_id: str) -> float | None:
        return self.data.get("rate_limits", {}).get(user_id)

    # Processed updates
    async def mark_update_processed(self, update_id: int) -> None:
        async with self.lock:
            updates = self.data.setdefault("processed_updates", [])
            updates.append({"update_id": update_id, "ts": asyncio.get_event_loop().time()})
            if len(updates) > MAX_PROCESSED_UPDATES:
                cutoff = asyncio.get_event_loop().time() - PROCESSED_UPDATES_TTL_HOURS * 3600
                self.data["processed_updates"] = [u for u in updates if u.get("ts", 0) > cutoff]
            self.dirty = True

    def is_update_processed(self, update_id: int) -> bool:
        for u in self.data.get("processed_updates", []):
            if u.get("update_id") == update_id:
                return True
        return False

    # Conversations
    async def get_user_history(self, user_id: str) -> list:
        return self.data.setdefault("conversations", {}).get(user_id, [])

    async def add_message(self, user_id: str, role: str, content: str) -> None:
        async with self.lock:
            hist = self.data.setdefault("conversations", {}).setdefault(user_id, [])
            hist.append({"role": role, "content": content[:MAX_TEXT_LENGTH]})
            if len(hist) > MAX_HISTORY:
                hist.pop(0)
            self.dirty = True

    async def clear_history(self, user_id: str) -> None:
        async with self.lock:
            self.data.setdefault("conversations", {})[user_id] = []
            self.dirty = True

    # User facts
    async def get_user_facts(self, user_id: str) -> list:
        return self.data.setdefault("user_facts", {}).get(user_id, [])

    async def add_facts(self, user_id: str, facts: list[str]) -> None:
        if not facts:
            return
        async with self.lock:
            existing = set(f.lower() for f in self.data.setdefault("user_facts", {}).setdefault(user_id, []))
            for fact in facts:
                if fact.lower() not in existing:
                    self.data["user_facts"][user_id].append(fact)
                    existing.add(fact.lower())
            self.dirty = True

    # Notes
    async def get_notes(self) -> list:
        return self.data.get("notes", [])[-10:]

    async def add_note(self, text: str) -> None:
        async with self.lock:
            self.data.setdefault("notes", []).append(
                {"text": text, "timestamp": datetime.now().isoformat()}
            )
            self.dirty = True

    async def clear_notes(self) -> None:
        async with self.lock:
            self.data["notes"] = []
            self.dirty = True

    # Costs
    async def log_costs(self, inp: int, out: int) -> None:
        async with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            costs = self.data.setdefault("costs", dict(DEFAULT_COSTS))
            costs["total_input_tokens"] = costs.get("total_input_tokens", 0) + inp
            costs["total_output_tokens"] = costs.get("total_output_tokens", 0) + out
            if costs.get("daily_date") != today:
                costs["daily_date"] = today
                costs["daily_input_tokens"] = 0
                costs["daily_output_tokens"] = 0
            costs["daily_input_tokens"] = costs.get("daily_input_tokens", 0) + inp
            costs["daily_output_tokens"] = costs.get("daily_output_tokens", 0) + out
            self.dirty = True

    async def get_costs(self) -> dict:
        return self.data.get("costs", {})


memory = MemoryManager(
    hf_token=settings.hf_token.get_secret_value(),
    dataset_repo=settings.dataset_repo,
)
