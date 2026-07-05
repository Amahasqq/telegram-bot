import os
import sys
import unittest.mock as um

# Provide dummy settings so `app.config.Settings()` validates at import time
# without requiring real secrets. Use setdefault so a real env still wins.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter")
os.environ.setdefault("HF_TOKEN", "test-hf")
os.environ.setdefault("DATASET_REPO", "test/test")
os.environ.setdefault("SPACE_URL", "test.hf.space")

# huggingface_hub performs network I/O on use; stub it so the suite stays
# fully offline. FastAPI/Starlette/httpx are intentionally left real so the
# integration tests exercise the actual ASGI app.
sys.modules.setdefault("huggingface_hub", um.MagicMock())

from collections.abc import AsyncGenerator  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


@pytest.fixture
def mock_hf_api():
    api = MagicMock()
    api.token = "test_token"
    api.hf_hub_download = AsyncMock(return_value="/tmp/bot_data.json")
    api.upload_file = AsyncMock()
    return api


@pytest_asyncio.fixture
async def memory_manager(mock_hf_api) -> AsyncGenerator:
    with patch("app.services.memory.MemoryManager._save_now", new_callable=AsyncMock):
        from app.services.memory import MemoryManager
        mm = MemoryManager(hf_token="test_token", dataset_repo="test/test")
        await mm.load()
        mm.start()
        yield mm
        await mm.stop()


@pytest.fixture
def mock_httpx_client():
    client = MagicMock()
    client.post = AsyncMock()
    client.get = AsyncMock()
    with patch("app.services.llm.get_client", return_value=client), \
         patch("app.services.search.get_client", return_value=client), \
         patch("app.services.trends.get_client", return_value=client):
        yield client
