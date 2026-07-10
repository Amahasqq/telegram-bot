from unittest.mock import MagicMock

import pytest

from app.services.trends import (
    get_hf_papers,
    get_lobsters,
)


def _json_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


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
async def test_source_failure_returns_empty(mock_httpx_client):
    mock_httpx_client.get.side_effect = Exception("network down")

    assert await get_hf_papers() == []
    assert await get_lobsters() == []
