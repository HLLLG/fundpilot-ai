from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_project_root() -> Path:
    """Monorepo dev uses repo root; Docker image uses /app (see apps/api/Dockerfile)."""
    override = os.getenv("FUND_AI_PROJECT_ROOT")
    if override:
        return Path(override)

    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "apps" / "api").is_dir() and (ancestor / "apps" / "web").is_dir():
            return ancestor

    # Standalone container: /app/app/config.py -> /app
    return here.parents[1]


PROJECT_ROOT = _resolve_project_root()

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
    cors_origins: str = "http://localhost:3001,http://127.0.0.1:3001"
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
    news_sources: str = "eastmoney,cls,announcement,macro"
    tactical_prompt_tuning_enabled: bool = True
    tactical_prompt_tuning_lookback_reports: int = 30
    sector_signal_backtest_enabled: bool = True
    sector_signal_backtest_days: int = 120
    sector_signal_backtest_min_triggers: int = 10
    news_summarize: bool = True
    news_summarize_model: str | None = None
    news_summarize_max_points: int = 5
    news_summarize_timeout_seconds: float = 60.0
    news_macro_topic: str = "上证指数"
    nav_trend_days: int = 66
    nav_trend_recent_sample: int = 8
    news_require_today_for_add: bool = True
    db_auto_import_path: Path | None = None
    sector_quotes_enabled: bool = True
    sector_quotes_ttl_seconds: int = 60
    sector_quotes_respect_manual: bool = False
    sector_quotes_discrepancy_warn: float = 0.5
    sector_quotes_auto_interval_seconds: int = 180
    sector_quotes_relay_url: str | None = None
    sector_quotes_relay_timeout_seconds: float = 2.5
    sector_quotes_relay_token: str | None = None
    sector_quotes_browser_enabled: bool = False
    sector_quotes_browser_command: str | None = None
    sector_intraday_browser_command: str | None = None
    sector_quotes_browser_timeout_seconds: float = 4.0
    ocr_preload: bool = True
    ocr_use_mobile_models: bool = True
    ocr_max_image_side: int = 1280
    # 截图识别引擎：auto（有 key 走云 VLM 否则本地）/ vlm（强制云，失败回退本地）/ local（强制本地不外传）
    ocr_provider: str = "auto"
    vlm_ocr_api_key: str | None = None
    vlm_ocr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vlm_ocr_model: str = "qwen3-vl-flash"
    vlm_ocr_timeout_seconds: float = 20.0
    jwt_secret: str = "fundpilot-dev-jwt-secret-change-me-32chars"
    jwt_access_expire_minutes: int = 43_200  # 30 days
    database_url: str | None = None
    cloudbase_env_id: str | None = None
    cloudbase_custom_login_key_path: Path | None = None
    cloudbase_api_base_url: str = "https://tcb-api.tencentcloudapi.com"
    cloudbase_auth_dev_mode: bool = False
    # 方案 A 默认关闭：美股 Tab 仅展示指数 + 汇率，不拉 QDII 穿透估值
    us_market_qdii_enabled: bool = False
    # 主题板块后台刷新：daemon 线程时段感知（盘中 15min / 收盘 1h），前台只读缓存
    theme_board_refresh_enabled: bool = True
    theme_board_refresh_interval_seconds: int = 900
    theme_board_refresh_idle_interval_seconds: int = 3600
    # 组合风险指标无风险利率（年化，小数；夏普/索提诺/Alpha 使用）
    risk_free_rate: float = 0.02

    @field_validator("risk_free_rate", mode="before")
    @classmethod
    def normalize_risk_free_rate(cls, value: object) -> float:
        """容错：用户填 2 表示 2% 时归一到 0.02；非法值回落到默认 0.02。"""
        if value is None:
            return 0.02
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.02
        return number / 100 if number > 1 else number

    @field_validator("cloudbase_custom_login_key_path", mode="before")
    @classmethod
    def normalize_cloudbase_key_path(cls, value: object) -> Path | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return Path(value)

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).strip()

    @field_validator("db_auto_import_path", mode="before")
    @classmethod
    def normalize_db_auto_import_path(cls, value: object) -> Path | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return Path(value)

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

    @field_validator("vlm_ocr_api_key", mode="before")
    @classmethod
    def normalize_vlm_ocr_api_key(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip().strip('"').strip("'")
        return cleaned or None

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def news_source_set(self) -> set[str]:
        return {
            part.strip().lower()
            for part in self.news_sources.split(",")
            if part.strip()
        }

    @property
    def resolved_news_summarize_model(self) -> str:
        return self.news_summarize_model or self.deepseek_model_fast

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def uses_mysql(self) -> bool:
        return bool(self.database_url and self.database_url.startswith("mysql"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_risk_free_rate() -> float:
    """年化无风险利率（小数）。默认 2%，可经 FUND_AI_RISK_FREE_RATE 覆盖。"""
    return get_settings().risk_free_rate


def refresh_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
