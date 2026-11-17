"""Environment configuration. Every field maps to a variable in ``.env.example``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "production", "test"] = "development"

    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_temperature: float = Field(default=0.25, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=1200, gt=0)
    # Gemini 2.5 'thinking' tokens: 0 turns thinking off (fastest). Unset = model default.
    llm_thinking_budget: int | None = Field(default=None, ge=0)

    knowledge_dir: Path = Path("knowledge")
    kb_reload_check_seconds: float = Field(default=5.0, ge=0)
    kb_max_context_tokens: int = Field(default=6000, gt=0)
    prompt_max_input_tokens: int = Field(default=12000, gt=0)

    storage_backend: Literal["sqlite", "memory"] = "sqlite"
    sqlite_path: Path = Path("data/chat.db")
    session_ttl_days: int = Field(default=30, gt=0)
    max_history_messages: int = Field(default=12, ge=0)
    max_message_length: int = Field(default=4000, gt=0)

    session_secret: SecretStr | None = None
    allowed_origins: str = ""
    rate_limit_per_minute: int = Field(default=10, gt=0)
    daily_message_cap: int = Field(default=200, gt=0)
    admin_api_key: SecretStr | None = None

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_message_content: bool = False
    port: int = Field(default=8000, gt=0, lt=65536)

    @field_validator(
        "gemini_api_key", "gemini_model", "session_secret", "admin_api_key", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def gemini_configured(self) -> bool:
        return self.gemini_api_key is not None and bool(self.gemini_model)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
