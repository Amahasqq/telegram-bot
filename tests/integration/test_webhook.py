from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.main import app


@pytest.fixture
def mock_memory():
    with patch("app.main.memory") as mock:
        mock.is_update_processed = MagicMock(return_value=False)
        mock.mark_update_processed = AsyncMock()
        mock.set_user_chat_id = AsyncMock()
        mock.get_user_history = AsyncMock(return_value=[])
        mock.get_user_facts = AsyncMock(return_value=[])
        mock.add_message = AsyncMock()
        mock.log_costs = AsyncMock()
        mock.data = {"conversations": {}}
        yield mock


def _valid_update(chat_id=123, user_id=456, text="hello"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
            "date": 1700000000,
        },
    }


@pytest.mark.asyncio
async def test_webhook_valid_update(mock_memory):
    with patch("app.main.handle_text") as mock_handle:
        mock_handle.return_value = {"method": "sendMessage", "chat_id": 123, "text": "AI response"}
        with patch("app.main.verify_webhook_secret", AsyncMock(return_value=True)):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post("/webhook", json=_valid_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "test"})

                assert response.status_code == 200
                data = response.json()
                assert data["method"] == "sendMessage"
                assert data["chat_id"] == 123
                assert data["text"] == "AI response"


@pytest.mark.asyncio
async def test_webhook_invalid_secret(mock_memory):
    with patch("app.main.verify_webhook_secret", AsyncMock(return_value=False)):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/webhook", json=_valid_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})

            assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_empty_body(mock_memory):
    with patch("app.main.verify_webhook_secret", AsyncMock(return_value=True)):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "test"})

            assert response.status_code == 200
            assert response.json() == {}


@pytest.mark.asyncio
async def test_webhook_deduplication(mock_memory):
    mock_memory.is_update_processed = MagicMock(return_value=True)
    with patch("app.main.verify_webhook_secret", AsyncMock(return_value=True)):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/webhook", json=_valid_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "test"})

            assert response.status_code == 200
            assert response.json() == {}


@pytest.mark.asyncio
async def test_health_endpoint(mock_memory):
    mock_memory.data = {"conversations": {"1": [], "2": []}}
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["users"] == 2
