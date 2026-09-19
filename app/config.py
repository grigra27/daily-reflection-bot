"""Application configuration loaded from the environment.

All secrets and environment-specific values live outside the source tree and
are provided through a local ``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN", default="")
    allowed_telegram_ids_raw: str = Field(alias="ALLOWED_TELEGRAM_IDS", default="")
    default_timezone: str = Field(alias="DEFAULT_TIMEZONE", default="Europe/Moscow")
    database_url: str = Field(alias="DATABASE_URL", default="sqlite:///data/reflection.db")
    default_checkin_time: str = Field(alias="DEFAULT_CHECKIN_TIME", default="21:30")
    default_reminder_time: str = Field(alias="DEFAULT_REMINDER_TIME", default="23:00")
    default_morning_time: str = Field(alias="DEFAULT_MORNING_TIME", default="08:30")
    log_level: str = Field(alias="LOG_LEVEL", default="INFO")

    @field_validator("allowed_telegram_ids_raw")
    @classmethod
    def _validate_ids(cls, value: str) -> str:
        for part in filter(None, (p.strip() for p in value.split(","))):
            if not part.lstrip("+").isdigit():
                raise ValueError(f"Invalid Telegram ID in ALLOWED_TELEGRAM_IDS: {part!r}")
        return value

    @property
    def allowed_telegram_ids(self) -> set[int]:
        return {int(p) for p in filter(None, (s.strip() for s in self.allowed_telegram_ids_raw.split(",")))}

    @property
    def is_prod(self) -> bool:
        return bool(self.telegram_bot_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
