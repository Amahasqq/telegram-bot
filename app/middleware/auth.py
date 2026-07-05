import hmac

from starlette.datastructures import Headers

from app.config import settings


async def verify_webhook_secret(headers: Headers) -> bool:
    token = headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = settings.telegram_webhook_secret.get_secret_value()
    return hmac.compare_digest(token, expected)
