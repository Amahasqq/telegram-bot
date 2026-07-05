import asyncio
import logging

from httpx import Timeout as HttpxTimeout
from httpx import AsyncClient

from app.constants import (
    HN_TIMEOUT,
    REDDIT_TIMEOUT,
    HN_TOP_STORIES,
    HN_RESULTS,
    REDDIT_POSTS_PER_SUB,
    REDDIT_RESULTS,
    REDDIT_SUBREDDITS,
)
from app.services.http_client import get_client

logger = logging.getLogger(__name__)


async def _fetch_hn_item(client: AsyncClient, item_id: int) -> dict | None:
    try:
        response = await client.get(
            f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
            timeout=HttpxTimeout(HN_TIMEOUT),
        )
        if response.status_code != 200:
            return None
        data = response.json()
        return {
            "title": data.get("title", ""),
            "url": data.get("url", f"https://news.ycombinator.com/item?id={item_id}"),
            "score": data.get("score", 0),
        }
    except Exception:
        return None


async def get_hackernews_trends() -> list[dict]:
    try:
        client = get_client()
        response = await client.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=HttpxTimeout(HN_TIMEOUT),
        )
        response.raise_for_status()
        ids = response.json()[:HN_TOP_STORIES]
        tasks = [_fetch_hn_item(client, item_id) for item_id in ids]
        results = await asyncio.gather(*tasks)
        items = [r for r in results if r is not None]
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[:HN_RESULTS]
    except Exception as e:
        logger.error("Hacker News error: %s", e)
        return []


async def _fetch_subreddit(client: AsyncClient, sub: str) -> list[dict]:
    try:
        response = await client.get(
            f"https://www.reddit.com/r/{sub}/hot.json?limit={REDDIT_POSTS_PER_SUB}",
            headers={"User-Agent": "TelegramAIBot/1.0"},
            timeout=HttpxTimeout(REDDIT_TIMEOUT),
        )
        if response.status_code != 200:
            return []
        posts = []
        for post in response.json().get("data", {}).get("children", []):
            data = post["data"]
            posts.append({
                "title": data.get("title", ""),
                "url": data.get("url", ""),
                "score": data.get("score", 0),
                "subreddit": sub,
            })
        return posts
    except Exception as e:
        logger.error("Reddit %s error: %s", sub, e)
        return []


async def get_reddit_trends() -> list[dict]:
    try:
        client = get_client()
        tasks = [_fetch_subreddit(client, sub) for sub in REDDIT_SUBREDDITS]
        results = await asyncio.gather(*tasks)
        all_posts = [post for sublist in results for post in sublist]
        all_posts.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_posts[:REDDIT_RESULTS]
    except Exception as e:
        logger.error("Reddit error: %s", e)
        return []
