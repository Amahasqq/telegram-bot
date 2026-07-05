from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: SecretStr
    telegram_webhook_secret: SecretStr
    openrouter_api_key: SecretStr
    google_genai_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    x_bearer_token: SecretStr | None = None
    allowed_user_id: int
    hf_token: SecretStr
    dataset_repo: str
    space_url: str

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
