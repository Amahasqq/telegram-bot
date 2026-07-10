from unittest.mock import MagicMock

import pytest

from app.services.trends import (
    get_hf_papers,
    get_lobsters,
    get_hn_ai,
    get_arxiv_papers,
    get_devto_articles,
    get_google_news,
)


def _json_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def _xml_response(text):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.text = text
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
async def test_hn_ai(mock_httpx_client):
    mock_httpx_client.get.return_value = _json_response({
        "hits": [
            {"objectID": "1", "title": "Story B", "url": "https://b", "points": 50},
            {"objectID": "2", "title": "Story A", "url": None, "points": 200},
        ]
    })

    result = await get_hn_ai()

    # Sorted by points desc; story without url falls back to item link.
    assert result[0]["title"] == "Story A"
    assert result[0]["url"] == "https://news.ycombinator.com/item?id=2"
    assert result[1]["url"] == "https://b"


@pytest.mark.asyncio
async def test_arxiv_papers(mock_httpx_client):
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Paper   A  with   spaces</title>
        <id>http://arxiv.org/abs/2401.00001v1</id>
      </entry>
      <entry>
        <title>Paper B</title>
        <id>http://arxiv.org/abs/2401.00002</id>
      </entry>
    </feed>"""
    mock_httpx_client.get.return_value = _xml_response(xml)

    result = await get_arxiv_papers()

    assert result[0]["title"] == "Paper A with spaces"  # collapsed whitespace
    assert result[0]["url"] == "https://arxiv.org/abs/2401.00001"
    assert result[1]["url"] == "https://arxiv.org/abs/2401.00002"


@pytest.mark.asyncio
async def test_devto_articles(mock_httpx_client):
    mock_httpx_client.get.return_value = _json_response([
        {"title": "Post A", "url": "https://dev.to/a", "positive_reactions_count": 10},
        {"title": "Post B", "url": "https://dev.to/b", "positive_reactions_count": 99},
    ])

    result = await get_devto_articles()

    assert result[0]["title"] == "Post B"  # sorted by reactions desc
    assert result[0]["url"] == "https://dev.to/b"


@pytest.mark.asyncio
async def test_google_news(mock_httpx_client):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Headline One - The Source</title>
          <link>https://news.google.com/rss/articles/abc</link>
        </item>
        <item>
          <title>Headline Two - Other</title>
          <link>https://news.google.com/rss/articles/def</link>
        </item>
      </channel>
    </rss>"""
    mock_httpx_client.get.return_value = _xml_response(xml)

    result = await get_google_news()

    # Source suffix (" - The Source") is stripped from the title.
    assert result[0]["title"] == "Headline One"
    assert result[0]["url"] == "https://news.google.com/rss/articles/abc"
    assert result[1]["title"] == "Headline Two"


@pytest.mark.asyncio
async def test_source_failure_returns_empty(mock_httpx_client):
    mock_httpx_client.get.side_effect = Exception("network down")

    assert await get_hf_papers() == []
    assert await get_lobsters() == []
    assert await get_hn_ai() == []
    assert await get_arxiv_papers() == []
    assert await get_devto_articles() == []
    assert await get_google_news() == []
