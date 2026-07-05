import asyncio
import json
import logging

from app.config import settings
from app.exceptions import ExternalAPIError
from app.services.memory import memory
from app.services.llm import call_openrouter
from app.services.search import search_web
from app.services.trends import get_hackernews_trends, get_reddit_trends
from app.utils.telegram import tg_resp

logger = logging.getLogger(__name__)

BRIEFING_PROMPT = """You are a tech briefing assistant. Create a concise summary of AI/tech news.

Hacker News data: {hn_data}
Reddit data: {reddit_data}
News data: {news_data}

Structure your response:
1. **Top Story**: 1-2 sentences about the most important news
2. **Community Buzz** (Hacker News + Reddit): Key discussions and trends
3. **Notable**: Other interesting items

Keep it brief, engaging, and informative."""


async def generate_briefing(chat_id: int) -> dict[str, object]:
    tasks = [get_hackernews_trends(), get_reddit_trends()]

    tavily_key = settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None
    if tavily_key:
        tasks.append(search_web("latest AI tech news today", tavily_key))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    hn_data = results[0] if len(results) > 0 and not isinstance(results[0], BaseException) else []
    reddit_data = results[1] if len(results) > 1 and not isinstance(results[1], BaseException) else []
    news_results = results[2] if len(results) > 2 and not isinstance(results[2], BaseException) else []

    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            logger.error("Briefing task %d error: %s", i, r)

    news_data = news_results[:3] if isinstance(news_results, list) else []

    prompt = BRIEFING_PROMPT.format(
        hn_data=json.dumps(hn_data, ensure_ascii=False)[:2000],
        reddit_data=json.dumps(reddit_data, ensure_ascii=False)[:2000],
        news_data=json.dumps(news_data, ensure_ascii=False)[:1000],
    )

    openrouter_key = settings.openrouter_api_key.get_secret_value()

    try:
        answer, usage = await call_openrouter(
            [
                {"role": "system", "content": "You are a tech briefing assistant. Be concise, engaging, and informative."},
                {"role": "user", "content": prompt},
            ],
            openrouter_key,
        )
    except ExternalAPIError as e:
        logger.error("Briefing generation error: %s", e)
        return tg_resp("sendMessage", chat_id, text="Failed to generate briefing. Please try again later.")

    briefing = f"📡 Daily Briefing\n\n{answer}"
    if usage:
        await memory.log_costs(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    logger.info("Briefing generated")
    return tg_resp("sendMessage", chat_id, text=briefing)
