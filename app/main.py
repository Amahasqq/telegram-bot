import time
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import httpx
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.constants import RATE_LIMIT, WEBHOOK_TIMEOUT
from app.exceptions import AppError, ExternalAPIError
from app.logging_config import setup_logging
from app.middleware.auth import verify_webhook_secret
from app.services.memory import memory
from app.services.http_client import close_client, get_client
from app.schemas.telegram import TelegramUpdate
from app.handlers.commands import handle_command
from app.handlers.messages import handle_text

setup_logging()
logger = logging.getLogger(__name__)

telegram_token = settings.telegram_bot_token.get_secret_value()
webhook_secret = settings.telegram_webhook_secret.get_secret_value()

_user_last_msg: dict[int, float] = {}


def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last = _user_last_msg.get(user_id, 0.0)
    if now - last < RATE_LIMIT:
        return True
    _user_last_msg[user_id] = now
    return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    get_client()
    await memory.load()
    memory.start()

    webhook_url = f"https://{settings.space_url}/webhook"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{telegram_token}/setWebhook",
                json={"url": webhook_url, "secret_token": webhook_secret},
                timeout=WEBHOOK_TIMEOUT,
            )
        logger.info("Webhook registered: %s", webhook_url)
    except Exception as e:
        logger.warning("Webhook registration failed (non-critical): %s", e)

    yield

    await memory.stop()
    await close_client()
    logger.info("Shutdown complete")


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def webhook_auth_middleware(request: Request, call_next):
    if request.url.path == "/webhook":
        if not await verify_webhook_secret(request.headers):
            return Response(status_code=403)
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "users": len(memory.data.get("conversations", {}))}


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, object]:
    data = await request.json()

    try:
        update = TelegramUpdate.model_validate(data)
    except Exception as e:
        logger.error("Validation error: %s", e)
        return {}

    if not await memory.claim_update(update.update_id):
        return {}

    if not update.message:
        return {}

    chat_id = update.message.chat.id if update.message.chat else None
    user_id = update.message.from_field.id if update.message.from_field else None

    if not chat_id or not user_id:
        return {}

    if settings.allowed_user_id is not None and user_id != settings.allowed_user_id:
        logger.info("Ignoring update from non-allowed user %s", user_id)
        return {}

    if is_rate_limited(user_id):
        return {"method": "sendMessage", "chat_id": chat_id, "text": "Please slow down. I need a moment between messages."}

    await memory.set_user_chat_id(str(user_id), chat_id)

    text = update.message.text or ""
    entities = update.message.entities or []

    if entities and entities[0].get("type") == "bot_command":
        try:
            return await handle_command(chat_id, user_id, text)
        except ExternalAPIError as e:
            logger.error("External API error for user %s: %s", user_id, e)
            return {"method": "sendMessage", "chat_id": chat_id, "text": "I'm having trouble connecting. Please try again later."}
        except Exception as e:
            logger.error("Command error for user %s: %s", user_id, e)
            return {"method": "sendMessage", "chat_id": chat_id, "text": "An error occurred. Please try again later."}

    try:
        return await handle_text(chat_id, user_id, text)
    except ExternalAPIError as e:
        logger.error("External API error for user %s: %s", user_id, e)
        return {"method": "sendMessage", "chat_id": chat_id, "text": "I'm having trouble connecting. Please try again later."}
    except Exception as e:
        logger.error("Handler error for user %s: %s", user_id, e)
        return {"method": "sendMessage", "chat_id": chat_id, "text": "An error occurred. Please try again later."}


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.error("App error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})
