from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.exceptions import ExternalAPIError
from app.services.llm import _call_model, call_openrouter, extract_facts


def _make_mock_response(status=200, content="Test response", prompt_tokens=10, completion_tokens=20):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
async def test_call_model_success(mock_httpx_client):
    mock_httpx_client.post.return_value = _make_mock_response()

    content, usage = await _call_model("test-model", [{"role": "user", "content": "hi"}], "api_key")

    assert content == "Test response"
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 20
    mock_httpx_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_call_openrouter_first_model_success():
    with patch("app.services.llm._call_model") as mock_call:
        mock_call.return_value = ("Response", {"prompt_tokens": 5, "completion_tokens": 10})

        content, usage = await call_openrouter([{"role": "user", "content": "hi"}], "api_key")

        assert content == "Response"
        assert usage["prompt_tokens"] == 5
        mock_call.assert_called_once()


@pytest.mark.asyncio
async def test_call_openrouter_fallback():
    with patch("app.services.llm._call_model") as mock_call:
        mock_call.side_effect = [
            httpx.HTTPStatusError("Model 1 failed", request=None, response=MagicMock(status_code=429)),
            httpx.HTTPStatusError("Model 2 failed", request=None, response=MagicMock(status_code=429)),
            ("Fallback response", {"prompt_tokens": 1, "completion_tokens": 2}),
        ]

        content, usage = await call_openrouter([{"role": "user", "content": "hi"}], "api_key")

        assert content == "Fallback response"
        assert mock_call.call_count == 3


@pytest.mark.asyncio
async def test_call_openrouter_all_fail():
    with patch("app.services.llm._call_model") as mock_call:
        mock_call.side_effect = httpx.HTTPStatusError("Always fails", request=None, response=MagicMock(status_code=500))

        with pytest.raises(ExternalAPIError):
            await call_openrouter([{"role": "user", "content": "hi"}], "api_key")

        assert mock_call.call_count >= 3


@pytest.mark.asyncio
async def test_extract_facts_valid_json():
    with patch("app.services.llm.call_openrouter") as mock_or:
        mock_or.return_value = ('["likes python", "works remotely"]', {})

        facts = await extract_facts("I work remotely and love Python", "api_key")

        assert facts == ["likes python", "works remotely"]


@pytest.mark.asyncio
async def test_extract_facts_empty():
    with patch("app.services.llm.call_openrouter") as mock_or:
        mock_or.return_value = ("[]", {})

        facts = await extract_facts("Hello world", "api_key")

        assert facts == []


@pytest.mark.asyncio
async def test_extract_facts_malformed_json():
    with patch("app.services.llm.call_openrouter") as mock_or:
        mock_or.return_value = ("not json at all", {})

        facts = await extract_facts("test", "api_key")

        assert facts == []


@pytest.mark.asyncio
async def test_extract_facts_markdown_wrapped():
    with patch("app.services.llm.call_openrouter") as mock_or:
        mock_or.return_value = ('```json\n["likes python"]\n```', {})

        facts = await extract_facts("test", "api_key")

        assert facts == ["likes python"]


@pytest.mark.asyncio
async def test_extract_facts_no_result():
    with patch("app.services.llm.call_openrouter") as mock_or:
        mock_or.return_value = ("", {})

        facts = await extract_facts("test", "api_key")

        assert facts == []
