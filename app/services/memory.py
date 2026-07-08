import asyncio
import json
import logging
import time
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
    USER_FACTS_MAX,
)

logger = logging.getLogger(__name__)

DATA_FILENAME = "bot_data.json"
LOCAL_DATA_PATH = "/tmp/bot_data.json"
REPO_TYPE = "dataset"


class MemoryManager:
    """Persists bot state to a Hugging Face Dataset with periodic background sync."""

    def __init__(self, hf_token: str, dataset_repo: str) -> None:
        self.api = HfApi(token=hf_token)
        self.dataset_repo = dataset_repo
        self.data: dict[str, Any] = {}
        self.dirty = False
        self.last_sync = 0.0
        self.lock = asyncio.Lock()
        self._bg_task: asyncio.Task[None] | None = None

    async def load(self) -> None:
        # Ensure the dataset repo exists (idempotent) so the first save can
        # upload into it instead of 404-ing on a missing repository.
        try:
            await asyncio.to_thread(
                self.api.create_repo,
                repo_id=self.dataset_repo,
                repo_type=REPO_TYPE,
                private=True,
                exist_ok=True,
            )
        except Exception as e:
            logger.warning("Dataset repo ensure failed: %s", e)

        try:
            path = await asyncio.to_thread(
                self.api.hf_hub_download,
                repo_id=self.dataset_repo,
                filename=DATA_FILENAME,
                repo_type=REPO_TYPE,
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
            "user_facts": raw.get("user_facts", {}),
            "processed_updates": raw.get("processed_updates", []),
            "user_chat_ids": raw.get("user_chat_ids", {}),
        }

    def _write_and_upload(self, payload: str) -> None:
        """Blocking file write + upload; always runs in a worker thread."""
        with open(LOCAL_DATA_PATH, "w", encoding="utf-8") as f:
            f.write(payload)
        self.api.upload_file(
            repo_id=self.dataset_repo,
            path_or_fileobj=LOCAL_DATA_PATH,
            path_in_repo=DATA_FILENAME,
            repo_type=REPO_TYPE,
            token=self.api.token,
        )

    async def _save_now(self, payload: str) -> None:
        await asyncio.to_thread(self._write_and_upload, payload)
        logger.info("Dataset saved")

    async def save(self, force: bool = False) -> None:
        # Serialize the snapshot under the lock (fast, in-memory), then perform
        # the slow file write + network upload OUTSIDE the lock so message
        # handlers are not blocked for the duration of the upload.
        async with self.lock:
            if not self.dirty and not force:
                return
            now = asyncio.get_running_loop().time()
            if not (force or now - self.last_sync > HF_SYNC_INTERVAL):
                return
            payload = json.dumps(self.data, ensure_ascii=False)
            self.last_sync = now
            self.dirty = False

        try:
            await self._save_now(payload)
        except Exception:
            self.dirty = True  # keep dirty so the next sync retries
            raise

    async def _bg_sync(self) -> None:
        while True:
            await asyncio.sleep(HF_SYNC_INTERVAL)
            try:
                await self.save()
            except Exception as e:
                # Never let a transient upload failure kill the sync loop.
                logger.error("Background sync failed: %s", e)

    def start(self) -> None:
        self._bg_task = asyncio.create_task(self._bg_sync())

    async def stop(self) -> None:
        if self._bg_task:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(self.save(force=True), timeout=30.0)
        except Exception as e:
            logger.error("Final save on shutdown failed (data may be lost): %s", e)

    # User chat_id mapping
    async def set_user_chat_id(self, user_id: str, chat_id: int) -> None:
        async with self.lock:
            self.data.setdefault("user_chat_ids", {})[user_id] = chat_id
            self.dirty = True

    # Processed updates (idempotency for Telegram webhook retries)
    async def claim_update(self, update_id: int) -> bool:
        """Atomically mark an update as processed.

        Returns True if the update is new (and now claimed), False if it was
        already processed. Doing check-and-mark under a single lock prevents the
        race where Telegram retries deliver the same update concurrently.
        """
        async with self.lock:
            updates = self.data.setdefault("processed_updates", [])
            if any(u.get("update_id") == update_id for u in updates):
                return False
            updates.append({"update_id": update_id, "ts": time.time()})
            if len(updates) > MAX_PROCESSED_UPDATES:
                cutoff = time.time() - PROCESSED_UPDATES_TTL_HOURS * 3600
                self.data["processed_updates"] = [u for u in updates if u.get("ts", 0) > cutoff]
            self.dirty = True
            return True

    # Conversations
    async def get_user_history(self, user_id: str) -> list[dict]:
        return list(self.data.get("conversations", {}).get(user_id, []))

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
    async def get_user_facts(self, user_id: str) -> list[str]:
        return list(self.data.get("user_facts", {}).get(user_id, []))

    async def add_facts(self, user_id: str, facts: list[str]) -> None:
        if not facts:
            return
        async with self.lock:
            stored = self.data.setdefault("user_facts", {}).setdefault(user_id, [])
            existing = {f.lower() for f in stored}
            for fact in facts:
                if fact.lower() not in existing:
                    stored.append(fact)
                    existing.add(fact.lower())
            if len(stored) > USER_FACTS_MAX:
                del stored[: len(stored) - USER_FACTS_MAX]
            self.dirty = True

    # Notes
    async def get_notes(self) -> list[dict]:
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


memory = MemoryManager(
    hf_token=settings.hf_token.get_secret_value(),
    dataset_repo=settings.dataset_repo,
)
