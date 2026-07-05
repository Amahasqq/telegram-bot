import asyncio
import json
import logging

from httpx import Timeout as HttpxTimeout

from app.constants import MODEL_CHAIN, OPENROUTER_URL, OPENROUTER_TIMEOUT, OPENROUTER_RETRIES
from app.exceptions import ExternalAPIError
from app.services.http_client import get_client

logger = logging.getLogger(__name__)

FACT_PROMPT = """Extract personal facts about the user from their message. Return ONLY a JSON array of strings describing what you learned.
If nothing to extract, return [].

Examples: ["Lives in New York", "Works as a software engineer", "Has a cat named Whiskers"]

Message: {text}"""


async def _call_model(model: str, messages: list, api_key: str) -> tuple[str, dict]:
    client = get_client()
    body = {"model": model, "messages": messages}
    response = await client.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=HttpxTimeout(OPENROUTER_TIMEOUT),
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage", {})
    return content, {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }


async def call_openrouter(messages: list, api_key: str) -> tuple[str, dict]:
    if not MODEL_CHAIN:
        raise ValueError("MODEL_CHAIN is empty")

    last_error: Exception | None = None
    for model in MODEL_CHAIN:
        for attempt in range(OPENROUTER_RETRIES):
            try:
                return await _call_model(model, messages, api_key)
            except Exception as e:
                last_error = e
                logger.warning("Model %s attempt %d failed: %s", model, attempt + 1, e)
                await asyncio.sleep(2**attempt)

    raise ExternalAPIError(f"All models failed: {last_error}")


async def extract_facts(text: str, api_key: str) -> list[str]:
    try:
        prompt = FACT_PROMPT.format(text=text[:1000])
        result, _ = await call_openrouter([{"role": "user", "content": prompt}], api_key)
        if not result:
            return []
        raw = result.strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        try:
            facts = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(facts, list):
            return []
        return [str(f).strip() for f in facts if str(f).strip()]
    except Exception as e:
        logger.error("Fact extraction error: %s", e)
        return []
