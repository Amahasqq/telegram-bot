import asyncio
import json
import logging
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
    "живо, информативно и по делу. Добавляй ссылки на источники."
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

Пропускай пустые разделы. Для каждого материала добавляй обычный URL из поля url, который идёт рядом с заголовком (не используй Markdown-ссылки).
Пиши живо и без воды."""


async def generate_briefing(chat_id: int) -> dict[str, object]:
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

    def _fmt(key: str, limit: int) -> str:
        return json.dumps(data.get(key, []), ensure_ascii=False)[:limit]

    prompt = BRIEFING_PROMPT.format(
        hn_data=_fmt("hn", BRIEFING_HN_CHARS),
        lobsters_data=_fmt("lobsters", BRIEFING_LOBSTERS_CHARS),
        reddit_data=_fmt("reddit", BRIEFING_REDDIT_CHARS),
        papers_data=_fmt("papers", BRIEFING_PAPERS_CHARS),
        github_data=_fmt("github", BRIEFING_GITHUB_CHARS),
        news_data=json.dumps(data.get("news", [])[:BRIEFING_NEWS_COUNT], ensure_ascii=False)[:BRIEFING_NEWS_CHARS],
    )

    openrouter_key = settings.openrouter_api_key.get_secret_value()

    try:
        answer, _ = await call_openrouter(
            [
                {"role": "system", "content": BRIEFING_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            openrouter_key,
        )
    except ExternalAPIError as e:
        logger.error("Briefing generation error: %s", e)
        return tg_resp("sendMessage", chat_id, text="Не удалось сформировать брифинг. Попробуйте позже.")

    prefix = "📡 Ежедневный брифинг\n\n"
    answer = truncate(answer, TELEGRAM_MAX_MSG - len(prefix))
    briefing = prefix + answer
    logger.info("Briefing generated")
    return tg_resp("sendMessage", chat_id, text=briefing, disable_web_page_preview=True)
