import asyncio
import logging
import re
import time
from collections.abc import Awaitable

from app.config import settings
from app.constants import (
    BRIEFING_NEWS_COUNT,
    BRIEFING_HN_CHARS,
    BRIEFING_REDDIT_CHARS,
    BRIEFING_NEWS_CHARS,
    BRIEFING_LOBSTERS_CHARS,
    BRIEFING_PAPERS_CHARS,
    BRIEFING_GITHUB_CHARS,
    BRIEFING_PARSE_MODE,
    BRIEFING_MAX_SOURCES,
    BRIEFING_CACHE_TTL,
    BRIEFING_MODEL,
    BRIEFING_EMPTY_MSG,
    TELEGRAM_MAX_MSG,
)
from app.exceptions import ExternalAPIError
from app.services.llm import call_openrouter
from app.services.search import search_web
from app.utils.helpers import truncate
from app.services.trends import (
    get_hackernews_trends,
    get_reddit_trends,
    get_hf_papers,
    get_lobsters,
    get_github_trending,
)
from app.utils.telegram import tg_resp

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM = (
    "Ты — ассистент технологического брифинга. Пиши на русском языке, "
    "живо, информативно и по делу. Пиши простым текстом: без разметки Markdown "
    "и без ссылок/URL в теле ответа — источники добавит бот отдельно."
)

BRIEFING_PROMPT = """Составь дайджест новостей об ИИ и технологиях на русском языке.

Hacker News: {hn_data}
Lobsters: {lobsters_data}
Reddit: {reddit_data}
Научные статьи (HF Daily Papers): {papers_data}
Трендовые репозитории GitHub: {github_data}
Новости (веб-поиск): {news_data}

Структура ответа:
1. **Главное**: 2-3 предложения о самой важной новости дня.
2. **Обсуждают** (Hacker News, Lobsters, Reddit): ключевые дискуссии сообщества.
3. **Исследования** (HF Papers): 2-3 заметные статьи.
4. **Проекты** (GitHub): трендовые репозитории.
5. **Интересное**: другие материалы из новостей.

Пропускай пустые разделы. Пиши простым текстом, БЕЗ Markdown-разметки и БЕЗ ссылок — источники бот добавит сам."""

_SOURCE_ORDER = ["hn", "reddit", "lobsters", "papers", "github", "news"]

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


def _fmt_compact(data: dict[str, list], key: str, limit: int) -> str:
    lines: list[str] = []
    items = data.get(key, [])
    if key == "news":
        items = items[:BRIEFING_NEWS_COUNT]
    for item in items:
        line = f"- {item.get('title', '')}"
        score = _item_score(item)
        if score != "":
            line += f" ({score})"
        url = item.get("url", "")
        if url:
            line += f" {url}"
        lines.append(line)
    return "\n".join(lines)[:limit]


def _build_sources(data: dict[str, list], max_items: int = BRIEFING_MAX_SOURCES) -> str:
    lines: list[str] = []
    for key in _SOURCE_ORDER:
        for item in data.get(key, []):
            title = (item.get("title", "") or "").strip()
            url = (item.get("url", "") or "").strip()
            if not title:
                continue
            if url and url.startswith(("http://", "https://")):
                lines.append(f"- [{_md_escape(title)}]({url})")
            else:
                lines.append(f"- {_md_escape(title)}")
            if len(lines) >= max_items:
                return "\n".join(lines)
    return "\n".join(lines)


async def _gather_sources(force: bool = False) -> dict[str, list]:
    global _cache, _cache_ts
    if not force and _cache is not None and (_now() - _cache_ts) < BRIEFING_CACHE_TTL:
        return _cache

    sources: dict[str, Awaitable[list]] = {
        "hn": get_hackernews_trends(),
        "lobsters": get_lobsters(),
        "reddit": get_reddit_trends(),
        "papers": get_hf_papers(),
        "github": get_github_trending(),
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


async def generate_briefing(chat_id: int) -> dict[str, object]:
    data = _dedup(await _gather_sources())

    if not any(data.get(key) for key in _SOURCE_ORDER):
        return tg_resp("sendMessage", chat_id, text=BRIEFING_EMPTY_MSG)

    prompt = BRIEFING_PROMPT.format(
        hn_data=_fmt_compact(data, "hn", BRIEFING_HN_CHARS),
        lobsters_data=_fmt_compact(data, "lobsters", BRIEFING_LOBSTERS_CHARS),
        reddit_data=_fmt_compact(data, "reddit", BRIEFING_REDDIT_CHARS),
        papers_data=_fmt_compact(data, "papers", BRIEFING_PAPERS_CHARS),
        github_data=_fmt_compact(data, "github", BRIEFING_GITHUB_CHARS),
        news_data=_fmt_compact(data, "news", BRIEFING_NEWS_CHARS),
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
        return tg_resp("sendMessage", chat_id, text="Не удалось сформировать брифинг. Попробуйте позже.")

    prefix = "📡 Ежедневный брифинг\n\n"
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
