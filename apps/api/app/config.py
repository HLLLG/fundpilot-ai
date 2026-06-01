from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]

# DeepSeek V4 系列 API 文档：单次输出上限 384K tokens
DEEPSEEK_MAX_OUTPUT_TOKENS = 384_000
DEEPSEEK_API_KEY_MIN_LENGTH = 24
PLACEHOLDER_DEEPSEEK_KEY_MARKERS = (
    "your-deepseek-key",
    "your-deepseek",
    "sk-your-",
    "changeme",
    "replace-me",
    "example",
)


class Settings(BaseSettings):
    app_name: str = "Fund AI Assistant"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    db_path: Path = PROJECT_ROOT / "data" / "app.db"
    upload_dir: Path = PROJECT_ROOT / "uploads"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_model_fast: str = "deepseek-v4-flash"
    deepseek_timeout_seconds: float = 300
    deepseek_max_tokens: int = DEEPSEEK_MAX_OUTPUT_TOKENS
    deepseek_max_tokens_report: int = DEEPSEEK_MAX_OUTPUT_TOKENS
    news_enabled: bool = True
    news_max_topics: int = 5
    news_per_topic: int = 5
    news_tool_max_rounds: int = 3

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="FUND_AI_",
        extra="ignore",
    )

    @field_validator("deepseek_api_key", mode="before")
    @classmethod
    def normalize_deepseek_api_key(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        cleaned = value.strip().strip('"').strip("'")
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if any(marker in lowered for marker in PLACEHOLDER_DEEPSEEK_KEY_MARKERS):
            return None
        if len(cleaned) < DEEPSEEK_API_KEY_MIN_LENGTH:
            return None
        return cleaned

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def refresh_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
