"""
Configuration module using Pydantic BaseSettings.
Loads environment variables from .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    """

    # Telegram Bot
    telegram_bot_token: str

    # Database (PostgreSQL + asyncpg)
    database_url: str
    database_echo: bool = False

    # Redis
    redis_url: str

    # Korapay API
    korapay_public_key: str
    korapay_secret_key: str
    korapay_base_url: str = "https://api.korapay.com/merchant/api/v1"

    # Celery & Message Queue
    celery_broker_url: str
    celery_result_backend: str

    # App Settings
    debug: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"
    api_host: str = "http://localhost:8000"
    bot_webhook_url: str | None = None
    escrow_release_hours: int = 48

    # Admin Settings
    admin_telegram_id: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Returns cached settings instance.
    Use this function throughout the app instead of creating Settings() multiple times.
    """
    return Settings()
