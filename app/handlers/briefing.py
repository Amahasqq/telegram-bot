import asyncio
import logging
import re
import time
from collections.abc import Awaitable
from datetime import date

from app.config import settings
from app.constants import (
    BRIEFING_HN_CHARS,
    BRIEFING_HN_AI_CHARS,
    BRIEFING_NEWS_CHARS,
    BRIEFING_LOBSTERS_CHARS,
    BRIEFING_PAPERS_CHARS,
    BRIEFING_ARXIV_CHARS,
    BRIEFING_DEVTO_CHARS,
    BRIEFING_PARSE_MODE,
    BRIEFING_MAX_SOURCES,
    BRIEFING_CACHE_TTL,
    BRIEFING_MODEL,
    BRIEFING_EMPTY_MSG,
    BRIEFING_DIGEST_MAX,
    TELEGRAM_MAX_MSG,
)
from app.exceptions import ExternalAPIError
from app.services.llm import call_openrouter
from app.services.search import search_web
from app.utils.helpers import truncate
from app.services.trends import (
    get_hackernews_trends,
    get_hn_ai,
    get_hf_papers,
    get_lobsters,
    get_arxiv_papers,
    get_devto_articles,
    get_google_news,
)
from app.utils.telegram import tg_resp

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM = (
    "Ты — ассистент технологического брифинга. Пиши на русском языке, "
    "живо, информативно и по делу. Пиши простым текстом: без разметки Markdown "
    "и без ссылок/URL в теле ответа — источники добавит бот отдельно."
)

BRIEFING_PROMPT = """На основе ВСЕХ перечисленных ниже источников (Hacker News, Lobsters, HF Papers, arXiv, Dev.to, Google News, Tavily) составь ежедневный брифинг об ИИ и технологиях на русском языке. Пиши простым текстом: без разметки Markdown и без ссылок/URL в теле ответа — источники бот добавит отдельно.

Источники:
{feed}

Структура ответа (используй эмодзи-заголовки, БЕЗ Markdown-разметки):

🔥 Главная новость дня
Одна самая важная новость или статья за день. Кратко: что это и почему важно (1-3 предложения).

💬 Что сейчас обсуждают
2-3 темы активных дискуссий и обсуждений в сообществе (Hacker News, Lobsters, Dev.to). По 1-2 строки на тему.

👀 На что обратить внимание
2-3 заметных исследования, тренда или релиза, на которые стоит взглянуть. По 1-2 строки на пункт.

Пропускай пустые разделы. Держи каждый раздел коротким (2-4 строки). Общий объём ответа — не более ~{digest_max} символов, чтобы всё поместилось в одно сообщение."""

_SOURCE_ORDER = ["hn", "hn_ai", "lobsters", "papers", "arxiv", "devto", "news"]

_SOURCE_LABELS = {
    "hn": "Hacker News",
    "hn_ai": "Hacker News (AI)",
    "lobsters": "Lobsters",
    "papers": "HF Papers",
    "arxiv": "arXiv",
    "devto": "Dev.to",
    "news": "Новости",
}

_cache: dict[str, list] | None = None
_cache_ts: float = 0.0


def _now() -> float:
    return time.monotonic()


def _normalize(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").lower()).strip()


def _dedup(data: dict[str, list]) -> dict[str, list]:
    seen: set[str] = set()
    out: dict[str, list] = {}
    for key in _SOURCE_ORDER:
        kept: list[dict] = []
        for item in data.get(key, []):
            norm = _normalize(item.get("title", ""))
            if not norm or norm in seen:
                continue
            seen.add(norm)
            kept.append(item)
        out[key] = kept
    return out


def _md_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _item_score(item: dict) -> object:
    return item.get("score") or item.get("upvotes") or item.get("stars") or ""


def _fmt_feed(data: dict[str, list], limit: int) -> str:
    blocks: list[str] = []
    for key in _SOURCE_ORDER:
        items = data.get(key, [])
        if not items:
            continue
        label = _SOURCE_LABELS.get(key, key)
        lines = [f"## {label}"]
        for item in items:
            line = f"- {item.get('title', '')}"
            score = _item_score(item)
            if score != "":
                line += f" ({score})"
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)[:limit]


def _build_sources(
    data: dict[str, list],
    per_source: int = 3,
    max_total: int = BRIEFING_MAX_SOURCES,
) -> str:
    lines: list[str] = []
    for key in _SOURCE_ORDER:
        for item in data.get(key, [])[:per_source]:
            title = (item.get("title", "") or "").strip()
            url = (item.get("url", "") or "").strip()
            if not title:
                continue
            if url and url.startswith(("http://", "https://")):
                lines.append(f"- [{_md_escape(title)}]({url})")
            else:
                lines.append(f"- {_md_escape(title)}")
            if len(lines) >= max_total:
                return "\n".join(lines)
    return "\n".join(lines)


def _briefing_fallback(chat_id: int, data: dict[str, list]) -> dict[str, object]:
    """Used when the LLM call fails: still return the collected source links."""
    prefix = f"📡 Ежедневный брифинг — {date.today():%d.%m.%Y}\n\n"
    sources_block = _build_sources(data)
    text = prefix + "Не удалось сформировать текст брифинга, но вот собранные источники:"
    if sources_block:
        text += "\n\nИсточники:\n" + sources_block
    return tg_resp(
        "sendMessage",
        chat_id,
        text=text[:TELEGRAM_MAX_MSG],
        parse_mode=BRIEFING_PARSE_MODE,
        disable_web_page_preview=True,
    )


async def _gather_sources(force: bool = False) -> dict[str, list]:
    global _cache, _cache_ts
    if not force and _cache is not None and (_now() - _cache_ts) < BRIEFING_CACHE_TTL:
        return _cache

    sources: dict[str, Awaitable[list]] = {
        "hn": get_hackernews_trends(),
        "hn_ai": get_hn_ai(),
        "lobsters": get_lobsters(),
        "papers": get_hf_papers(),
        "arxiv": get_arxiv_papers(),
        "devto": get_devto_articles(),
        "news": get_google_news(),
    }

    tavily_key = settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None
    if tavily_key:
        sources["news"] = search_web("latest AI tech news today", tavily_key)

    keys = list(sources.keys())
    results = await asyncio.gather(*sources.values(), return_exceptions=True)

    data: dict[str, list] = {}
    for key, result in zip(keys, results):
        if isinstance(result, BaseException):
            logger.error("Briefing source %s error: %s", key, result)
            data[key] = []
        else:
            data[key] = result if isinstance(result, list) else []

    _cache = data
    _cache_ts = _now()
    return data


_FEED_CHARS = {
    "hn": BRIEFING_HN_CHARS,
    "hn_ai": BRIEFING_HN_AI_CHARS,
    "lobsters": BRIEFING_LOBSTERS_CHARS,
    "papers": BRIEFING_PAPERS_CHARS,
    "arxiv": BRIEFING_ARXIV_CHARS,
    "devto": BRIEFING_DEVTO_CHARS,
    "news": BRIEFING_NEWS_CHARS,
}


async def generate_briefing(chat_id: int) -> dict[str, object]:
    data = _dedup(await _gather_sources())

    if not any(data.get(key) for key in _SOURCE_ORDER):
        return tg_resp("sendMessage", chat_id, text=BRIEFING_EMPTY_MSG)

    feed_parts = [_fmt_feed({k: data.get(k, [])[: _FEED_CHARS.get(k, 1500)] for k in _SOURCE_ORDER}, 6000)]

    prompt = BRIEFING_PROMPT.format(
        feed=feed_parts[0],
        digest_max=BRIEFING_DIGEST_MAX,
    )

    openrouter_key = settings.openrouter_api_key.get_secret_value()
    model = settings.briefing_model or BRIEFING_MODEL

    try:
        answer, _ = await call_openrouter(
            [
                {"role": "system", "content": BRIEFING_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            openrouter_key,
            model=model,
        )
    except ExternalAPIError as e:
        logger.error("Briefing generation error: %s", e)
        return _briefing_fallback(chat_id, data)

    prefix = f"📡 Ежедневный брифинг — {date.today():%d.%m.%Y}\n\n"
    sources_block = _build_sources(data)

    budget = TELEGRAM_MAX_MSG - len(prefix) - len(sources_block) - 2
    if budget < 200:
        sources_block = ""
        answer = truncate(answer, TELEGRAM_MAX_MSG - len(prefix))
    else:
        answer = truncate(answer, budget)

    text = prefix + answer
    if sources_block:
        text += "\n\nИсточники:\n" + sources_block

    logger.info("Briefing generated")
    return tg_resp(
        "sendMessage",
        chat_id,
        text=text,
        parse_mode=BRIEFING_PARSE_MODE,
        disable_web_page_preview=True,
    )
