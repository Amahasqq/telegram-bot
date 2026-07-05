import logging

from httpx import Timeout as HttpxTimeout

from app.constants import TAVILY_TIMEOUT, TAVILY_MAX_RESULTS, TAVILY_QUERY_LENGTH
from app.services.http_client import get_client

logger = logging.getLogger(__name__)

_tavily_remaining = 1000


async def search_web(query: str, api_key: str) -> list[dict]:
    global _tavily_remaining
    if _tavily_remaining < 50:
        return []
    try:
        client = get_client()
        response = await client.post(
            "https://api.tavily.com/search",
            json={"query": query[:TAVILY_QUERY_LENGTH], "max_results": TAVILY_MAX_RESULTS},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=HttpxTimeout(TAVILY_TIMEOUT),
        )
        response.raise_for_status()
        data = response.json()
        if "X-RateLimit-Remaining" in response.headers:
            _tavily_remaining = int(response.headers["X-RateLimit-Remaining"])
        return data.get("results", [])
    except Exception as e:
        logger.error("Tavily error: %s", e)
        return []
