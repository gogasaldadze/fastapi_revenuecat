"""Application settings, read from environment variables and an optional .env file."""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. See docs/configuration.md for each field."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/app"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 24 * 30

    revenuecat_secret_key: str = ""
    revenuecat_webhook_secret: str = ""
    revenuecat_entitlement_id: str = "premium"
    revenuecat_api_url: str = "https://api.revenuecat.com/v1"
    revenuecat_timeout_seconds: float = 10.0
    allow_sandbox_subscriptions: bool = False
    entitlement_cache_ttl_seconds: int = 300
    store_webhook_events: bool = True

    free_daily_units: int = 20
    premium_daily_units: int = 1000
    feature_weights: dict[str, int] = {"basic": 1, "heavy": 10}
    fail_open_features: set[str] = {"basic"}

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, value: str) -> str:
        """Rewrite plain postgres URLs, as Railway and Heroku provide them, for asyncpg."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value.removeprefix(prefix)
        return value

    @field_validator("feature_weights")
    @classmethod
    def positive_weights(cls, value: dict[str, int]) -> dict[str, int]:
        """Reject zero or negative weights, which would make a feature free."""
        bad = sorted(name for name, weight in value.items() if weight < 1)
        if bad:
            raise ValueError(f"feature weights must be at least 1: {bad}")
        return value

    @property
    def sandbox_allowed(self) -> bool:
        """Sandbox purchases count only in debug or when explicitly allowed."""
        return self.debug or self.allow_sandbox_subscriptions


settings = Settings()
