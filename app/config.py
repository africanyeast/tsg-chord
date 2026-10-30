from functools import lru_cache

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://wumi:change-me@db:5432/wumi"
    mcp_bearer_token: str | None = None
    mcp_owner_bearer_token: str | None = None
    admin_bearer_token: str | None = None
    paystack_secret_key: str | None = None
    paystack_public_key: str | None = None
    public_base_url: AnyHttpUrl = "http://localhost:8000"
    reconciler_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Checked here, not in a pydantic validator, because a validation error would print the key.
    if (settings.paystack_secret_key or "").startswith("sk_live_"):
        raise RuntimeError("PAYSTACK_SECRET_KEY is a live key. Wumi runs on Paystack test keys only.")
    return settings
