from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    ttlock_webhook_secret: SecretStr = Field(min_length=32)
    ha_webhook_url: AnyHttpUrl
    max_body_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    rate_limit_per_minute: int = Field(default=30, ge=1, le=600)
    dedupe_ttl_seconds: int = Field(default=600, ge=30, le=86_400)
    ha_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10)
    log_level: str = "INFO"

    @field_validator("ttlock_webhook_secret")
    @classmethod
    def reject_placeholder_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(set(secret)) < 10 or "change" in secret.lower():
            raise ValueError("TTLOCK_WEBHOOK_SECRET must be high entropy")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
