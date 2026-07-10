from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import ExternalAPIError
from app.handlers import briefing as briefing_module
from app.utils.helpers import truncate


def _settings(tavily=None, briefing_model=None):
    s = patch("app.handlers.briefing.settings").start()
    s.tavily_api_key = tavily
    s.openrouter_api_key = MagicMock()
    s.openrouter_api_key.get_secret_value.return_value = "key"
    s.briefing_model = briefing_model
    return s


def _reset_cache():
    briefing_module._cache = None
    briefing_module._cache_ts = 0.0


@pytest.fixture(autouse=True)
def _reset():
    _reset_cache()
    yield
    patch.stopall()


@pytest.mark.asyncio
async def test_briefing_dedup_across_sources():
    item = {"title": "Same Big News", "url": "https://example.com/a", "score": 10}
    with patch("app.handlers.briefing.call_openrouter") as mock_or:
        mock_or.return_value = ("Текст", {"prompt_tokens": 1, "completion_tokens": 1})
        with patch("app.handlers.briefing.get_hackernews_trends", AsyncMock(return_value=[item])), \
             patch("app.handlers.briefing.get_lobsters", AsyncMock(return_value=[])), \
             patch("app.handlers.briefing.get_hf_papers", AsyncMock(return_value=[])):
            _settings()
            result = await briefing_module.generate_briefing(123)

    prompt = mock_or.call_args.args[0][1]["content"]
    assert prompt.count("Same Big News") == 1
    assert "[Same Big News](https://example.com/a)" in result["text"]


@pytest.mark.asyncio
async def test_briefing_empty_result_no_llm():
    with patch("app.handlers.briefing.call_openrouter") as mock_or:
        with patch("app.handlers.briefing.get_hackernews_trends", AsyncMock(return_value=[])), \
             patch("app.handlers.briefing.get_lobsters", AsyncMock(return_value=[])), \
             patch("app.handlers.briefing.get_hf_papers", AsyncMock(return_value=[])):
            _settings(tavily=None)
            result = await briefing_module.generate_briefing(123)

    mock_or.assert_not_called()
    assert "Источники" not in result["text"]
    assert "собрать" in result["text"]


@pytest.mark.asyncio
async def test_briefing_prompt_structure():
    with patch("app.handlers.briefing.call_openrouter") as mock_or:
        mock_or.return_value = ("Текст", {})
        with patch("app.handlers.briefing.get_hackernews_trends", AsyncMock(return_value=[{"title": "X", "url": "https://x.com"}])), \
             patch("app.handlers.briefing.get_lobsters", AsyncMock(return_value=[])), \
             patch("app.handlers.briefing.get_hf_papers", AsyncMock(return_value=[])):
            _settings()
            await briefing_module.generate_briefing(123)

    prompt = mock_or.call_args.args[0][1]["content"]
    for marker in ("🔥 Главное", "💬 Обсуждают", "🔬 Исследования", "✨ Интересное"):
        assert marker in prompt
    assert "3500" in prompt


@pytest.mark.asyncio
async def test_briefing_external_api_error():
    with patch("app.handlers.briefing.call_openrouter", side_effect=ExternalAPIError("boom")):
        with patch("app.handlers.briefing.get_hackernews_trends", AsyncMock(return_value=[{"title": "X", "url": "https://x.com"}])), \
             patch("app.handlers.briefing.get_lobsters", AsyncMock(return_value=[])), \
             patch("app.handlers.briefing.get_hf_papers", AsyncMock(return_value=[])):
            _settings()
            result = await briefing_module.generate_briefing(123)

    assert "Не удалось сформировать брифинг" in result["text"]


@pytest.mark.asyncio
async def test_briefing_cache_reuses_sources():
    src = AsyncMock(return_value=[{"title": "Cached", "url": "https://c.com"}])
    with patch("app.handlers.briefing.call_openrouter", return_value=("Текст", {})):
        with patch("app.handlers.briefing.get_hackernews_trends", src), \
             patch("app.handlers.briefing.get_lobsters", AsyncMock(return_value=[])), \
             patch("app.handlers.briefing.get_hf_papers", AsyncMock(return_value=[])):
            _settings()
            await briefing_module.generate_briefing(123)
            await briefing_module.generate_briefing(123)

    src.assert_awaited_once()


def test_truncate_word_boundary():
    text = "слово " * 50
    out = truncate(text, 30)
    assert len(out) <= 31
    assert out.endswith("…")
    assert "слово" in out


def test_truncate_short_unchanged():
    assert truncate("привет", 100) == "привет"
