from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Sequence

from pydantic import Field, computed_field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    api_id: int = Field(..., alias="API_ID")
    api_hash: str = Field(..., alias="API_HASH")
    telethon_session_string: str = Field("", alias="TELETHON_SESSION_STRING")
    database_url: str = Field(
        "sqlite+aiosqlite:///./sharechannel.sqlite3",
        alias="DATABASE_URL",
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    use_redis: bool = Field(True, alias="USE_REDIS")
    global_rate_limit_per_sec: float = Field(1.0, alias="GLOBAL_RATE_LIMIT_PER_SEC")
    chat_rate_limit_per_sec: float = Field(1.0, alias="CHAT_RATE_LIMIT_PER_SEC")
    new_post_delay_seconds: int = Field(45, alias="NEW_POST_DELAY_SECONDS")
    backup_chat_id: int | None = Field(default=None, alias="BACKUP_CHAT_ID")
    backup_interval_hours: float = Field(18.0, alias="BACKUP_INTERVAL_HOURS")
    hash_rounds: int = Field(12, alias="HASH_ROUNDS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")
    timezone: str = Field("UTC", alias="TIMEZONE")

    @computed_field
    @property
    def is_dev(self) -> bool:
        url = self.database_url.lower()
        return url.startswith("sqlite")

    @computed_field
    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @computed_field
    @property
    def admin_password(self) -> str:
        password_file = self.project_root / "password.txt"
        if password_file.exists():
            with open(password_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        return line
        return "admin123"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:

    return AppSettings()


settings = get_settings()

__all__ = ["AppSettings", "get_settings", "settings"]
