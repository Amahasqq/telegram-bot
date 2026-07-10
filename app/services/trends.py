import asyncio
import logging
import re
import xml.etree.ElementTree as ET

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
    HN_ALGOLIA_URL,
    HN_ALGOLIA_TIMEOUT,
    HN_ALGOLIA_RESULTS,
    HN_ALGOLIA_QUERY,
    ARXIV_URL,
    ARXIV_TIMEOUT,
    ARXIV_RESULTS,
    ARXIV_CATS,
    DEVTO_URL,
    DEVTO_TIMEOUT,
    DEVTO_RESULTS,
    DEVTO_TAG,
    GOOGLE_NEWS_URL,
    GOOGLE_NEWS_TIMEOUT,
    GOOGLE_NEWS_RESULTS,
    GOOGLE_NEWS_QUERY,
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


async def get_hn_ai() -> list[dict]:
    """AI-scoped Hacker News stories via the Algolia search API (free, no key)."""
    try:
        client = get_client()
        response = await client.get(
            HN_ALGOLIA_URL,
            params={"tags": "story", "query": HN_ALGOLIA_QUERY, "hitsPerPage": HN_ALGOLIA_RESULTS},
            timeout=HttpxTimeout(HN_ALGOLIA_TIMEOUT),
        )
        response.raise_for_status()
        items = []
        for hit in response.json().get("hits", []):
            item_id = hit.get("objectID", "")
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
            items.append({
                "title": hit.get("title", ""),
                "url": url,
                "score": hit.get("points", 0) or 0,
            })
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[:HN_ALGOLIA_RESULTS]
    except Exception as e:
        logger.error("HN AI error: %s", e)
        return []


async def get_arxiv_papers() -> list[dict]:
    """Recent AI papers from arXiv (free, no key, Atom XML)."""
    try:
        client = get_client()
        params: dict[str, str | int] = {
            "search_query": ARXIV_CATS,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": ARXIV_RESULTS,
        }
        response = await client.get(ARXIV_URL, params=params, timeout=HttpxTimeout(ARXIV_TIMEOUT))
        response.raise_for_status()
        root = ET.fromstring(response.text)
        items = []
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = " ".join((entry.findtext("{http://www.w3.org/2005/Atom}title") or "").split())
            raw_id = entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
            arxiv_id = re.sub(r"v\d+$", "", raw_id.rsplit("/", 1)[-1])
            items.append({
                "title": title,
                "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
                "score": "",
            })
        return items[:ARXIV_RESULTS]
    except Exception as e:
        logger.error("arXiv error: %s", e)
        return []


async def get_devto_articles() -> list[dict]:
    """Developer articles tagged 'ai' from Dev.to (free, no key)."""
    try:
        client = get_client()
        params: dict[str, str | int] = {"tag": DEVTO_TAG, "top": 1, "per_page": DEVTO_RESULTS}
        response = await client.get(DEVTO_URL, params=params, timeout=HttpxTimeout(DEVTO_TIMEOUT))
        response.raise_for_status()
        items = []
        for article in response.json()[:DEVTO_RESULTS]:
            items.append({
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "score": article.get("positive_reactions_count", 0) or 0,
            })
        items.sort(key=lambda x: x["score"], reverse=True)
        return items
    except Exception as e:
        logger.error("Dev.to error: %s", e)
        return []


async def get_google_news() -> list[dict]:
    """AI/tech news via Google News RSS (free, no key)."""
    try:
        client = get_client()
        params = {"q": GOOGLE_NEWS_QUERY, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        response = await client.get(GOOGLE_NEWS_URL, params=params, timeout=HttpxTimeout(GOOGLE_NEWS_TIMEOUT))
        response.raise_for_status()
        root = ET.fromstring(response.text)
        items = []
        for item in root.iter("item"):
            raw_title = item.findtext("title") or ""
            title = raw_title
            if " - " in title:
                title = title.rsplit(" - ", 1)[0].strip()
            link = item.findtext("link") or ""
            if title:
                items.append({"title": title, "url": link, "score": ""})
        return items[:GOOGLE_NEWS_RESULTS]
    except Exception as e:
        logger.error("Google News error: %s", e)
        return []
