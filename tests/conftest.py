import sys
import unittest.mock as um

for mod_name in ["huggingface_hub", "fastapi", "fastapi.middleware", "fastapi.middleware.cors", "starlette", "starlette.responses", "starlette.datastructures", "starlette.middleware", "uvicorn"]:
    sys.modules[mod_name] = um.MagicMock()

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


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
