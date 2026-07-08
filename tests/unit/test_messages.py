import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import ExternalAPIError
from app.handlers import messages
from app.constants import AI_TEMP_UNAVAILABLE, TELEGRAM_MAX_MSG


@pytest.mark.asyncio
async def test_basic_response():
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(return_value=("Hi there", {}))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()):
        result = await messages.handle_text(123, 1, "hello")

    assert result["method"] == "sendMessage"
    assert result["chat_id"] == 123
    assert result["text"] == "Hi there"


@pytest.mark.asyncio
async def test_search_on_russian_keyword():
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(return_value=("ответ", {}))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[{"title": "t", "content": "c", "url": "u"}])) as mock_search, \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()), \
         patch("app.handlers.messages.settings") as mock_settings:
        mock_settings.tavily_api_key = MagicMock()
        mock_settings.openrouter_api_key = MagicMock()
        await messages.handle_text(123, 1, "как сделать поиск")

    mock_search.assert_awaited()


@pytest.mark.asyncio
async def test_search_skipped_without_tavily():
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(return_value=("ответ", {}))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[])) as mock_search, \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()), \
         patch("app.handlers.messages.settings") as mock_settings:
        mock_settings.tavily_api_key = None
        mock_settings.openrouter_api_key = MagicMock()
        await messages.handle_text(123, 1, "поиск информации")

    mock_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_answer_truncated():
    long = "x" * 5000
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(return_value=(long, {}))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()):
        result = await messages.handle_text(123, 1, "hello")

    assert len(result["text"]) <= TELEGRAM_MAX_MSG


@pytest.mark.asyncio
async def test_facts_skipped_on_short_text():
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(return_value=("ok", {}))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()) as mock_facts:
        await messages.handle_text(123, 1, "ок")

    await asyncio.sleep(0)
    mock_facts.assert_not_awaited()


@pytest.mark.asyncio
async def test_facts_run_on_long_text():
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(return_value=("ok", {}))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()) as mock_facts:
        await messages.handle_text(123, 1, "I live in New York and work as a software engineer")

    await asyncio.sleep(0)
    mock_facts.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_api_error_returns_unavailable():
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(side_effect=ExternalAPIError("boom"))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()) as mock_add, \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()), \
         patch("app.handlers.messages.settings") as mock_settings:
        mock_settings.tavily_api_key = None
        mock_settings.openrouter_api_key = MagicMock()
        result = await messages.handle_text(123, 1, "hello")

    assert result["method"] == "sendMessage"
    assert result["text"] == AI_TEMP_UNAVAILABLE
    mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_search_results_injected_into_prompt():
    captured: dict = {}

    async def fake_or(messages_list, api_key):
        captured["messages"] = messages_list
        return ("ok", {})

    with patch("app.handlers.messages.call_openrouter", new=fake_or), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[{"title": "t", "content": "c", "url": "u"}])) as mock_search, \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()), \
         patch("app.handlers.messages.settings") as mock_settings:
        mock_settings.tavily_api_key = MagicMock()
        mock_settings.openrouter_api_key = MagicMock()
        await messages.handle_text(123, 1, "как сделать поиск")

    mock_search.assert_awaited()
    assert "[Search results:]" in captured["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_no_parse_mode_in_response():
    with patch("app.handlers.messages.call_openrouter", new=AsyncMock(return_value=("hi", {}))), \
         patch("app.handlers.messages.search_web", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_history", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "get_user_facts", new=AsyncMock(return_value=[])), \
         patch.object(messages.memory, "add_message", new=AsyncMock()), \
         patch("app.handlers.messages._extract_and_save_facts", new=AsyncMock()), \
         patch("app.handlers.messages.settings") as mock_settings:
        mock_settings.tavily_api_key = None
        mock_settings.openrouter_api_key = MagicMock()
        result = await messages.handle_text(123, 1, "hello")

    assert "parse_mode" not in result
