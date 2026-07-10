from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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


def _patch_sources(**values):
    mapping = {
        "hn": "get_hackernews_trends",
        "hn_ai": "get_hn_ai",
        "lobsters": "get_lobsters",
        "papers": "get_hf_papers",
        "arxiv": "get_arxiv_papers",
        "devto": "get_devto_articles",
        "news": "get_google_news",
    }
    stack = ExitStack()
    for short, full in mapping.items():
        val = values.get(short, [])
        mock = val if isinstance(val, (AsyncMock, MagicMock, Mock)) else AsyncMock(return_value=val)
        stack.enter_context(patch(f"app.handlers.briefing.{full}", mock))
    return stack


@pytest.mark.asyncio
async def test_briefing_dedup_across_sources():
    item = {"title": "Same Big News", "url": "https://example.com/a", "score": 10}
    with patch("app.handlers.briefing.call_openrouter") as mock_or:
        mock_or.return_value = ("Текст", {"prompt_tokens": 1, "completion_tokens": 1})
        with _patch_sources(hn=[item], lobsters=[item]):
            _settings()
            result = await briefing_module.generate_briefing(123)

    prompt = mock_or.call_args.args[0][1]["content"]
    assert prompt.count("Same Big News") == 1
    assert "[Same Big News](https://example.com/a)" in result["text"]


@pytest.mark.asyncio
async def test_briefing_empty_result_no_llm():
    with patch("app.handlers.briefing.call_openrouter") as mock_or:
        with _patch_sources():
            _settings(tavily=None)
            result = await briefing_module.generate_briefing(123)

    mock_or.assert_not_called()
    assert "Источники" not in result["text"]
    assert "собрать" in result["text"]


@pytest.mark.asyncio
async def test_briefing_prompt_structure():
    with patch("app.handlers.briefing.call_openrouter") as mock_or:
        mock_or.return_value = ("Текст", {})
        with _patch_sources(hn=[{"title": "X", "url": "https://x.com"}]):
            _settings()
            await briefing_module.generate_briefing(123)

    prompt = mock_or.call_args.args[0][1]["content"]
    for marker in ("🔥 Главная новость дня", "💬 Что сейчас обсуждают", "👀 На что обратить внимание"):
        assert marker in prompt
    assert "1800" in prompt


@pytest.mark.asyncio
async def test_briefing_external_api_error_falls_back_to_sources():
    with patch("app.handlers.briefing.call_openrouter", side_effect=ExternalAPIError("boom")):
        with _patch_sources(hn=[{"title": "X", "url": "https://x.com"}]):
            _settings()
            result = await briefing_module.generate_briefing(123)

    assert "Источники" in result["text"]
    assert "[X](https://x.com)" in result["text"]


@pytest.mark.asyncio
async def test_briefing_cache_reuses_sources():
    hn_mock = AsyncMock(return_value=[{"title": "Cached", "url": "https://c.com"}])
    with patch("app.handlers.briefing.call_openrouter", return_value=("Текст", {})):
        with _patch_sources(hn=hn_mock):
            _settings()
            await briefing_module.generate_briefing(123)
            await briefing_module.generate_briefing(123)

    hn_mock.assert_awaited_once()


def test_truncate_word_boundary():
    text = "слово " * 50
    out = truncate(text, 30)
    assert len(out) <= 31
    assert out.endswith("…")
    assert "слово" in out


def test_truncate_short_unchanged():
    assert truncate("привет", 100) == "привет"
