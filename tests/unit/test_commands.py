from unittest.mock import AsyncMock, patch

import pytest

from app.handlers.commands import handle_command
from app.handlers.briefing import generate_briefing


@pytest.mark.asyncio
async def test_start_command():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.clear_history = AsyncMock()

        result = await handle_command(123, 1, "/start")

        assert result["method"] == "sendMessage"
        assert result["chat_id"] == 123
        assert "AI assistant" in str(result["text"])
        mock_memory.clear_history.assert_not_called()


@pytest.mark.asyncio
async def test_clear_command():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.clear_history = AsyncMock()

        result = await handle_command(123, 1, "/clear")

        assert result["method"] == "sendMessage"
        assert result["chat_id"] == 123
        assert "cleared" in str(result["text"]).lower()
        mock_memory.clear_history.assert_awaited_once_with("1")


@pytest.mark.asyncio
async def test_note_command_saves():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.add_note = AsyncMock()

        result = await handle_command(123, 1, "/note buy milk")

        assert result["method"] == "sendMessage"
        assert "saved" in str(result["text"]).lower()
        mock_memory.add_note.assert_awaited_once_with("buy milk")


@pytest.mark.asyncio
async def test_note_command_empty():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.add_note = AsyncMock()

        result = await handle_command(123, 1, "/note")

        assert result["method"] == "sendMessage"
        assert "Usage" in str(result["text"])
        mock_memory.add_note.assert_not_called()


@pytest.mark.asyncio
async def test_notes_with_notes():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.get_notes = AsyncMock(return_value=[
            {"text": "note1", "timestamp": "2024-01-01"},
            {"text": "note2", "timestamp": "2024-01-02"},
        ])

        result = await handle_command(123, 1, "/notes")

        assert result["method"] == "sendMessage"
        text = str(result["text"])
        assert "note1" in text
        assert "note2" in text


@pytest.mark.asyncio
async def test_notes_without_notes():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.get_notes = AsyncMock(return_value=[])

        result = await handle_command(123, 1, "/notes")

        assert result["method"] == "sendMessage"
        assert "No notes" in str(result["text"])


@pytest.mark.asyncio
async def test_clearnotes_command():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.clear_notes = AsyncMock()

        result = await handle_command(123, 1, "/clearnotes")

        assert result["method"] == "sendMessage"
        assert "deleted" in str(result["text"]).lower()
        mock_memory.clear_notes.assert_awaited_once()


@pytest.mark.asyncio
async def test_costs_command():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.get_costs = AsyncMock(return_value={
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "daily_input_tokens": 100,
            "daily_output_tokens": 50,
            "daily_date": "2024-01-01",
        })

        result = await handle_command(123, 1, "/costs")

        assert result["method"] == "sendMessage"
        text = str(result["text"])
        assert "Cost stats" in text
        assert "1000" in text
        assert "500" in text


@pytest.mark.asyncio
async def test_unknown_command():
    with patch("app.handlers.commands.memory") as mock_memory:
        mock_memory.clear_history = AsyncMock()

        result = await handle_command(123, 1, "/unknown")

        assert result["method"] == "sendMessage"
        assert "Unknown" in str(result["text"])


@pytest.mark.asyncio
async def test_briefing_command():
    with patch("app.handlers.briefing.memory") as mock_memory:
        mock_memory.log_costs = AsyncMock()
        with patch("app.handlers.briefing.call_openrouter") as mock_or:
            mock_or.return_value = ("Test briefing content", {"prompt_tokens": 10, "completion_tokens": 20})
            with patch("app.handlers.briefing.get_hackernews_trends", AsyncMock(return_value=[])), \
                 patch("app.handlers.briefing.get_reddit_trends", AsyncMock(return_value=[])), \
                 patch("app.handlers.briefing.get_lobsters", AsyncMock(return_value=[])), \
                 patch("app.handlers.briefing.get_hf_papers", AsyncMock(return_value=[])), \
                 patch("app.handlers.briefing.get_github_trending", AsyncMock(return_value=[])):
                with patch("app.handlers.briefing.settings") as mock_settings:
                    mock_settings.tavily_api_key = None

                    result = await generate_briefing(123)

                    assert result["method"] == "sendMessage"
                    assert "брифинг" in str(result["text"]).lower()
