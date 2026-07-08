import asyncio
import logging
import os
import re

from app.config import settings
from app.constants import FACT_MIN_LEN, SEARCH_KEYWORDS, TELEGRAM_MAX_MSG
from app.exceptions import ExternalAPIError
from app.services.memory import memory
from app.services.llm import call_openrouter, extract_facts
from app.services.search import search_web
from app.utils.telegram import tg_resp
from app.utils.helpers import build_system_prompt, build_messages, truncate

logger = logging.getLogger(__name__)

# Keep strong references to fire-and-forget tasks so they are not garbage
# collected mid-execution (see CPython asyncio.create_task docs).
_background_tasks: set[asyncio.Task] = set()

PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts",
)
SYSTEM_PROMPT_PATH = os.path.join(PROMPTS_DIR, "system.txt")
with open(SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
    BASE_SYSTEM_PROMPT = f.read()


async def handle_text(chat_id: int, user_id: int, text: str) -> dict[str, object]:
    history: list[dict] = []
    user_facts: list[str] = []
    search_results: list[dict] = []

    try:
        history = await memory.get_user_history(str(user_id))
        user_facts = await memory.get_user_facts(str(user_id))
    except Exception:
        pass

    words = set(re.findall(r"\w+", text.lower()))
    needs_search = bool(SEARCH_KEYWORDS & words)

    if needs_search:
        tavily_key = settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None
        if tavily_key:
            try:
                search_results = await search_web(text, tavily_key)
            except Exception:
                pass

    sys_prompt = build_system_prompt(BASE_SYSTEM_PROMPT, user_facts)
    messages = build_messages(sys_prompt, history, text, search_results)

    openrouter_key = settings.openrouter_api_key.get_secret_value()

    try:
        answer, _ = await call_openrouter(messages, openrouter_key)
    except ExternalAPIError as e:
        logger.error("OpenRouter error for user %s: %s", user_id, e)
        return tg_resp("sendMessage", chat_id, text="I'm having trouble connecting to the AI. Please try again later.")

    answer = truncate(answer, TELEGRAM_MAX_MSG)

    try:
        await memory.add_message(str(user_id), "user", truncate(text))
        await memory.add_message(str(user_id), "assistant", answer)
        if len(text.strip()) >= FACT_MIN_LEN:
            task = asyncio.create_task(_extract_and_save_facts(text, user_id, openrouter_key))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.error("Memory error for user %s: %s", user_id, e)

    return tg_resp("sendMessage", chat_id, text=answer)


async def _extract_and_save_facts(text: str, user_id: int, openrouter_key: str) -> None:
    try:
        facts = await extract_facts(text, openrouter_key)
        if facts:
            await memory.add_facts(str(user_id), facts)
    except Exception as e:
        logger.error("Fact extraction error for user %s: %s", user_id, e)
