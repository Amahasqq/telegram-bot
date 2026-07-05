import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from httpx import Timeout as HttpxTimeout
from httpx import AsyncClient

from app.config import settings
from app.constants import (
    HN_TIMEOUT,
    REDDIT_TIMEOUT,
    HN_TOP_STORIES,
    HN_RESULTS,
    REDDIT_POSTS_PER_SUB,
    REDDIT_RESULTS,
    REDDIT_SUBREDDITS,
    REDDIT_OAUTH_TOKEN_URL,
    REDDIT_API_BASE,
    REDDIT_USER_AGENT,
    REDDIT_TOKEN_TTL,
    HF_PAPERS_URL,
    HF_PAPERS_TIMEOUT,
    HF_PAPERS_RESULTS,
    LOBSTERS_URL,
    LOBSTERS_TIMEOUT,
    LOBSTERS_RESULTS,
    GITHUB_SEARCH_URL,
    GITHUB_TIMEOUT,
    GITHUB_RESULTS,
    GITHUB_TOPIC,
    GITHUB_TREND_DAYS,
    GITHUB_USER_AGENT,
)
from app.services.http_client import get_client

logger = logging.getLogger(__name__)

_reddit_token: str | None = None
_reddit_token_exp: float = 0.0
_reddit_token_lock = asyncio.Lock()


async def _get_reddit_token(client: AsyncClient) -> str | None:
    """Fetch (and cache) an app-only OAuth token, or None if not configured."""
    global _reddit_token, _reddit_token_exp

    client_id = settings.reddit_client_id.get_secret_value() if settings.reddit_client_id else None
    client_secret = (
        settings.reddit_client_secret.get_secret_value() if settings.reddit_client_secret else None
    )
    if not client_id or not client_secret:
        return None

    if _reddit_token and time.time() < _reddit_token_exp:
        return _reddit_token

    async with _reddit_token_lock:
        # Re-check inside the lock: another coroutine may have refreshed it.
        if _reddit_token and time.time() < _reddit_token_exp:
            return _reddit_token
        try:
            response = await client.post(
                REDDIT_OAUTH_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
                headers={"User-Agent": REDDIT_USER_AGENT},
                timeout=HttpxTimeout(REDDIT_TIMEOUT),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logger.error("Reddit token error: %s", e)
            return None

        _reddit_token = payload.get("access_token")
        expires_in = min(int(payload.get("expires_in", 3600)), REDDIT_TOKEN_TTL)
        _reddit_token_exp = time.time() + expires_in
        return _reddit_token


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


async def _fetch_subreddit(client: AsyncClient, sub: str, token: str) -> list[dict]:
    try:
        response = await client.get(
            f"{REDDIT_API_BASE}/r/{sub}/hot?limit={REDDIT_POSTS_PER_SUB}",
            headers={
                "User-Agent": REDDIT_USER_AGENT,
                "Authorization": f"Bearer {token}",
            },
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
        token = await _get_reddit_token(client)
        if not token:
            logger.info("Reddit credentials not configured; skipping Reddit trends")
            return []
        tasks = [_fetch_subreddit(client, sub, token) for sub in REDDIT_SUBREDDITS]
        results = await asyncio.gather(*tasks)
        all_posts = [post for sublist in results for post in sublist]
        all_posts.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_posts[:REDDIT_RESULTS]
    except Exception as e:
        logger.error("Reddit error: %s", e)
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
            headers={"User-Agent": GITHUB_USER_AGENT},
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


async def get_github_trending() -> list[dict]:
    """Recently created, most-starred repos on a topic (unauthenticated search)."""
    try:
        client = get_client()
        since = (datetime.now(timezone.utc) - timedelta(days=GITHUB_TREND_DAYS)).strftime("%Y-%m-%d")
        response = await client.get(
            GITHUB_SEARCH_URL,
            params={
                "q": f"topic:{GITHUB_TOPIC} created:>{since}",
                "sort": "stars",
                "order": "desc",
                "per_page": GITHUB_RESULTS,
            },
            headers={
                "User-Agent": GITHUB_USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
            timeout=HttpxTimeout(GITHUB_TIMEOUT),
        )
        response.raise_for_status()
        items = []
        for repo in response.json().get("items", []):
            items.append({
                "title": repo.get("full_name", ""),
                "url": repo.get("html_url", ""),
                "stars": repo.get("stargazers_count", 0),
                "description": repo.get("description", "") or "",
            })
        return items
    except Exception as e:
        logger.error("GitHub trending error: %s", e)
        return []
