import asyncio
import logging

from httpx import Timeout as HttpxTimeout
from httpx import AsyncClient

from app.constants import (
    HN_TIMEOUT,
    HN_TOP_STORIES,
    HN_RESULTS,
    HF_PAPERS_URL,
    HF_PAPERS_TIMEOUT,
    HF_PAPERS_RESULTS,
    LOBSTERS_URL,
    LOBSTERS_TIMEOUT,
    LOBSTERS_RESULTS,
    HTTP_USER_AGENT,
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


async def get_hf_papers() -> list[dict]:
    """Curated trending AI papers from Hugging Face Daily Papers (no auth)."""
    try:
        client = get_client()
        response = await client.get(HF_PAPERS_URL, timeout=HttpxTimeout(HF_PAPERS_TIMEOUT))
        response.raise_for_status()
        items = []
        for entry in response.json():
            paper = entry.get("paper", entry)
            arxiv_id = paper.get("id", "")
            items.append({
                "title": paper.get("title", ""),
                "url": f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else "",
                "upvotes": paper.get("upvotes", 0),
            })
        items.sort(key=lambda x: x["upvotes"], reverse=True)
        return items[:HF_PAPERS_RESULTS]
    except Exception as e:
        logger.error("HF papers error: %s", e)
        return []


async def get_lobsters() -> list[dict]:
    """AI-tagged stories from Lobsters (HN-like community, no auth)."""
    try:
        client = get_client()
        response = await client.get(
            LOBSTERS_URL,
            headers={"User-Agent": HTTP_USER_AGENT},
            timeout=HttpxTimeout(LOBSTERS_TIMEOUT),
        )
        response.raise_for_status()
        items = []
        for post in response.json()[:LOBSTERS_RESULTS]:
            items.append({
                "title": post.get("title", ""),
                "url": post.get("url") or post.get("comments_url", ""),
                "score": post.get("score", 0),
            })
        return items
    except Exception as e:
        logger.error("Lobsters error: %s", e)
        return []
