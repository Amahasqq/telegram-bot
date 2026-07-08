from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: SecretStr
    telegram_webhook_secret: SecretStr
    openrouter_api_key: SecretStr
    tavily_api_key: SecretStr | None = None
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None
    allowed_user_id: int | None = None
    briefing_model: str | None = None
    hf_token: SecretStr
    dataset_repo: str
    space_url: str

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()  # type: ignore[call-arg]
