"""
Configuration module using Pydantic BaseSettings.
Loads environment variables from .env file.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    telegram_webhook_secret: str | None = None
    escrow_release_hours: int = 48

    # Admin Settings
    admin_telegram_id: str | None = None
    admin_api_key: str | None = None
    korapay_webhook_secret: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator(
        "allowed_hosts",
        "api_host",
        "bot_webhook_url",
        "admin_telegram_id",
        "admin_api_key",
        "telegram_webhook_secret",
        "korapay_webhook_secret",
        mode="before",
    )
    @classmethod
    def _normalize_optional_strings(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("escrow_release_hours")
    @classmethod
    def _validate_escrow_release_hours(cls, value: int) -> int:
        if value < 1 or value > 168:
            raise ValueError("ESCROW_RELEASE_HOURS must be between 1 and 168")
        return value

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def cors_allow_origins(self) -> list[str]:
        hosts = self.allowed_hosts_list
        if not hosts:
            return ["*"]

        normalized: list[str] = []
        for host in hosts:
            if host == "*":
                normalized.append("*")
                continue
            if host.startswith(("http://", "https://")):
                normalized.append(host)
            else:
                normalized.append(f"https://{host}")
        return normalized


@lru_cache
def get_settings() -> Settings:
    """
    Returns cached settings instance.
    Use this function throughout the app instead of creating Settings() multiple times.
    """
    return Settings()
