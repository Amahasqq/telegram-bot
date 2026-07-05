import httpx
from httpx import Timeout, Limits
from app.constants import OPENROUTER_TIMEOUT

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=Timeout(OPENROUTER_TIMEOUT, connect=10.0),
            limits=Limits(max_connections=20, max_keepalive_connections=5),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None
