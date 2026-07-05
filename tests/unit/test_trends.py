from unittest.mock import MagicMock, patch

import pytest

import app.services.trends as trends
from app.services.trends import (
    get_reddit_trends,
    get_hf_papers,
    get_lobsters,
    get_github_trending,
)


def _json_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


@pytest.fixture(autouse=True)
def reset_token():
    trends._reddit_token = None
    trends._reddit_token_exp = 0.0
    yield
    trends._reddit_token = None
    trends._reddit_token_exp = 0.0


def _token_response(access_token="tok", expires_in=3600):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"access_token": access_token, "expires_in": expires_in}
    return resp


def _listing_response(sub):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "children": [
                {"data": {"title": f"{sub} post", "url": f"https://r/{sub}", "score": 42}},
            ]
        }
    }
    return resp


@pytest.mark.asyncio
async def test_reddit_skipped_without_credentials(mock_httpx_client):
    with patch("app.services.trends.settings") as mock_settings:
        mock_settings.reddit_client_id = None
        mock_settings.reddit_client_secret = None

        result = await get_reddit_trends()

    assert result == []
    mock_httpx_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_reddit_oauth_flow(mock_httpx_client):
    mock_httpx_client.post.return_value = _token_response()
    mock_httpx_client.get.side_effect = lambda url, **kw: _listing_response(url.rsplit("/r/", 1)[-1].split("/")[0])

    with patch("app.services.trends.settings") as mock_settings:
        mock_settings.reddit_client_id = MagicMock()
        mock_settings.reddit_client_id.get_secret_value.return_value = "cid"
        mock_settings.reddit_client_secret = MagicMock()
        mock_settings.reddit_client_secret.get_secret_value.return_value = "secret"

        result = await get_reddit_trends()

    # One token request, sorted posts from all configured subreddits.
    mock_httpx_client.post.assert_called_once()
    assert len(result) == len(trends.REDDIT_SUBREDDITS)
    assert all("title" in p and "url" in p for p in result)


@pytest.mark.asyncio
async def test_reddit_token_failure_returns_empty(mock_httpx_client):
    mock_httpx_client.post.side_effect = Exception("401 Unauthorized")

    with patch("app.services.trends.settings") as mock_settings:
        mock_settings.reddit_client_id = MagicMock()
        mock_settings.reddit_client_id.get_secret_value.return_value = "cid"
        mock_settings.reddit_client_secret = MagicMock()
        mock_settings.reddit_client_secret.get_secret_value.return_value = "secret"

        result = await get_reddit_trends()

    assert result == []


@pytest.mark.asyncio
async def test_hf_papers(mock_httpx_client):
    mock_httpx_client.get.return_value = _json_response([
        {"paper": {"id": "2401.00001", "title": "Paper A", "upvotes": 5}},
        {"paper": {"id": "2401.00002", "title": "Paper B", "upvotes": 42}},
    ])

    result = await get_hf_papers()

    # Sorted by upvotes desc; arxiv id turned into a papers URL.
    assert result[0]["title"] == "Paper B"
    assert result[0]["url"] == "https://huggingface.co/papers/2401.00002"


@pytest.mark.asyncio
async def test_lobsters(mock_httpx_client):
    mock_httpx_client.get.return_value = _json_response([
        {"title": "Story A", "url": "https://a", "score": 10},
        {"title": "Text post", "url": "", "comments_url": "https://lobste.rs/s/x", "score": 3},
    ])

    result = await get_lobsters()

    assert len(result) == 2
    assert result[0]["url"] == "https://a"
    assert result[1]["url"] == "https://lobste.rs/s/x"  # falls back to comments_url


@pytest.mark.asyncio
async def test_github_trending(mock_httpx_client):
    mock_httpx_client.get.return_value = _json_response({
        "items": [
            {"full_name": "org/repo", "html_url": "https://gh/org/repo", "stargazers_count": 100, "description": "d"},
        ]
    })

    result = await get_github_trending()

    assert result[0]["title"] == "org/repo"
    assert result[0]["stars"] == 100


@pytest.mark.asyncio
async def test_source_failure_returns_empty(mock_httpx_client):
    mock_httpx_client.get.side_effect = Exception("network down")

    assert await get_hf_papers() == []
    assert await get_lobsters() == []
    assert await get_github_trending() == []
