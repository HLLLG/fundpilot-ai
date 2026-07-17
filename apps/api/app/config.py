from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from typing import Literal

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

# Bounded JSON reports use a latency-safe default far below provider ceilings.
DEEPSEEK_DEFAULT_OUTPUT_TOKENS = 32_768
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
    # 可选：正则匹配额外 Origin（CloudBase 静态托管默认 *.webapps.tcloudbase.com）
    cors_origin_regex: str | None = None
    db_path: Path = PROJECT_ROOT / "data" / "app.db"
    upload_dir: Path = PROJECT_ROOT / "uploads"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_model_fast: str = "deepseek-v4-flash"
    deepseek_timeout_seconds: float = 300
    # The provider supports a much larger ceiling, but reserving it for every
    # bounded JSON report increases scheduling latency without improving output.
    deepseek_max_tokens: int = DEEPSEEK_DEFAULT_OUTPUT_TOKENS
    deepseek_max_tokens_report: int = DEEPSEEK_DEFAULT_OUTPUT_TOKENS
    # HTTPX retries only connection establishment failures, never a response
    # that may already have started and may already be billable.
    deepseek_connection_retries: int = 2
    news_enabled: bool = True
    news_max_topics: int = 5
    news_per_topic: int = 5
    news_tool_max_rounds: int = 1
    news_sources: str = "eastmoney,cls,announcement,macro"
    # 旧日报复盘口径尚未按真实 T+N 收益与成熟样本重建，禁止默认参与 Prompt 调参。
    # 保留环境变量覆盖能力，仅供修复后的受控验证显式开启。
    tactical_prompt_tuning_enabled: bool = False
    tactical_prompt_tuning_lookback_reports: int = 30
    sector_signal_backtest_enabled: bool = True
    sector_signal_backtest_days: int = 120
    sector_signal_backtest_min_triggers: int = 10
    news_summarize: bool = True
    news_summarize_model: str | None = None
    news_summarize_max_points: int = 5
    news_summarize_timeout_seconds: float = 60.0
    news_fetch_timeout_seconds: float = 20.0
    news_prefetch_total_timeout_seconds: float = 45.0
    # 基金公告与市场/行业主题使用独立预算和缓存契约，避免持仓数挤占 news_max_topics。
    news_announcement_max_funds: int = 20
    news_announcement_per_fund: int = 3
    news_announcement_cache_ttl_seconds: int = 21_600
    news_announcement_prefetch_total_timeout_seconds: float = 20.0
    # Phase B 可交易性：申购状态短缓存，费率规则日级缓存；历史 decision_at
    # 只能读取当时已经存在的快照，禁止用当前页面回填历史决策。
    fund_tradeability_status_cache_ttl_seconds: int = 900
    fund_tradeability_fee_cache_ttl_seconds: int = 86_400
    fund_tradeability_status_timeout_seconds: float = 20.0
    fund_tradeability_fee_timeout_seconds: float = 30.0
    fund_tradeability_current_window_seconds: int = 600
    # Phase C fund-disclosure look-through. Fast reports stay store-only; deep
    # reports may refresh current aging/stale disclosures within a bounded batch.
    fund_holdings_context_max_funds: int = 40
    fund_holdings_context_live_max_funds: int = 8
    fund_holdings_context_workers: int = 4
    fund_holdings_context_total_timeout_seconds: float = 18.0
    fund_holdings_context_fast_timeout_seconds: float = 2.0
    fund_holdings_refresh_check_ttl_seconds: int = 21_600
    fund_holdings_refresh_retry_ttl_seconds: int = 900
    news_macro_topic: str = "上证指数"
    # 拉满 252 让日报/荐基与持仓详情弹窗预热共享 fund_nav_cache（key: code+days）。
    # 旧 nav_trend_days env 仍兼容（fallback 映射到 nav_cache_pull_days），过渡期一版。
    nav_cache_pull_days: int = 252
    nav_trend_window: int = 66
    nav_trend_recent_sample: int = 8
    # 批量净值预热：单次子进程拉多只基金净值（import akshare 一次），
    # 替代逐只各起子进程各 import 的开销。失败自动回退逐只路径。
    akshare_nav_batch_enabled: bool = True
    akshare_nav_batch_workers: int = 6
    news_require_today_for_add: bool = True
    db_auto_import_path: Path | None = None
    sector_quotes_enabled: bool = True
    # 覆盖 auto_interval 直至下次后台刷新（默认 180s 间隔 + 60s 余量）
    sector_quotes_ttl_seconds: int = 240
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
    # 基金名称全集仅用于 OCR/模糊查码预热；受限网络、测试和只跑 API 的部署可关闭，
    # 实际查码时仍会按需加载，不改变业务契约。
    fund_name_preload_enabled: bool = True
    ocr_preload: bool = True
    ocr_use_mobile_models: bool = True
    ocr_max_image_side: int = 1280
    # 截图识别引擎：auto（有 key 走云 VLM 否则本地）/ vlm（强制云，失败回退本地）/ local（强制本地不外传）
    ocr_provider: str = "auto"
    vlm_ocr_api_key: str | None = None
    vlm_ocr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vlm_ocr_model: str = "qwen-vl-ocr"
    vlm_ocr_timeout_seconds: float = 20.0
    # qwen-vl-ocr 图像缩放：min/max_pixels 作为 image_url 同级字段传入（每 1024 像素≈1 图像 token）
    vlm_ocr_min_pixels: int = 3072
    vlm_ocr_max_pixels: int = 8388608
    # 上传前压缩（best-effort）：转 JPEG 减小上传体积/延迟；token 由 max_pixels 控制，与文件体积无关
    vlm_ocr_compress_enabled: bool = True
    vlm_ocr_jpeg_quality: int = 85
    vlm_ocr_max_image_side: int = 2000
    jwt_secret: str = "fundpilot-dev-jwt-secret-change-me-32chars"
    jwt_access_expire_minutes: int = 43_200  # 30 days
    database_url: str | None = None
    # Cross-worker account write serialization. A bounded wait fails with 503
    # instead of allowing two stale read-modify-write operations to overlap.
    portfolio_mutation_lock_timeout_seconds: float = 30.0
    # ``None`` selects the safe default: enabled for single-process SQLite
    # development, disabled for MySQL where requests may hit another worker.
    holdings_memory_cache_enabled: bool | None = None
    factor_ic_publish_token: str | None = None
    # D2 decision-quality snapshots use a dedicated read-only credential.  It
    # is deliberately not shared with JWT or factor snapshot publication.
    decision_quality_read_token: str | None = None
    # D5.1 paired prompt shadowing is opt-in and never changes the champion
    # response.  The secret is used only for deterministic assignment; leaving
    # it unset makes an enabled deployment fail closed for shadow eligibility.
    prompt_shadow_enabled: bool = False
    prompt_shadow_assignment_secret: str | None = None
    prompt_shadow_assignment_key_id: str = "prompt-shadow-assignment-v1"
    prompt_shadow_sample_basis_points: int = 10_000
    prompt_shadow_max_challenger_calls_per_day: int = 100
    prompt_shadow_worker_batch_size: int = 8
    prompt_shadow_lease_seconds: int = 180
    prompt_shadow_challenger_deadline_seconds: int = 900
    factor_ic_stale_after_days: int = 30
    cloudbase_env_id: str | None = None
    # 方案 A 默认关闭：美股 Tab 仅展示指数 + 汇率，不拉 QDII 穿透估值
    us_market_qdii_enabled: bool = False
    # 主题板块后台刷新：daemon 线程时段感知（A 股活跃 20min / 休市 3h），前台只读缓存
    theme_board_refresh_enabled: bool = True
    theme_board_refresh_interval_seconds: int = 1200  # 盘中/美股活跃时段每 20min
    theme_board_refresh_idle_interval_seconds: int = 10800  # 非活跃时段每 3h（兼容旧 env）
    market_shared_idle_interval_seconds: int = 10800  # 非 A 股/美股活跃时段后台刷新间隔
    # 持仓详情：按用户内存缓存 + 后台预热（分时/净值/详情）
    holding_detail_cache_ttl_seconds: int = 300
    holding_intraday_warmup_enabled: bool = True
    # 全市场基金→板块离线预计算（fund_primary_sectors_global）
    fund_primary_sector_global_enabled: bool = True
    fund_primary_sector_global_benchmark_ttl_days: int = 30
    fund_primary_sector_global_holdings_ttl_days: int = 90
    fund_primary_sector_precompute_enabled: bool = True
    fund_primary_sector_precompute_batch_size: int = 150
    fund_primary_sector_precompute_interval_hours: int = 12
    fund_primary_sector_precompute_startup_delay_seconds: int = 300
    # 规则（业绩基准/持仓穿透）都推不出主题时，用 DeepSeek 兜底分类（按基金代码全局缓存，只调用一次）
    fund_primary_sector_llm_infer_enabled: bool = True
    # 应用启动后延迟一次性扫描存量持仓，把历史遗留的空板块用最新规则链（含 LLM）补全
    fund_primary_sector_backfill_enabled: bool = True
    fund_primary_sector_backfill_startup_delay_seconds: int = 90
    # 组合风险指标无风险利率（年化，小数；夏普/索提诺/Alpha 使用）
    risk_free_rate: float = 0.02
    # 大盘情绪温度计（M1.1）：新高/新低家数（可回测校准）+ 涨跌停/炸板（当日快照）+ 两融环比
    market_breadth_enabled: bool = True
    market_breadth_timeout_seconds: float = 4.0
    # 盘中赚钱效应准实时刷新与硬守卫资格：默认 5 分钟刷新、连续交易 10 分钟过期、开盘 5 分钟后才准入。
    market_breadth_live_refresh_interval_seconds: int = 300
    market_breadth_live_freshness_seconds: int = 600
    market_breadth_live_guard_delay_minutes: int = 5
    # 量价背离信号回测（M1.3）
    flow_divergence_backtest_enabled: bool = True
    # M6：双向 guard 灰度开关。shadow（默认）——M2.1/M4 的升级判定只标注"若启用会被
    # 升级为 XX"，不真正改变最终 action/剔除候选；enforced——真正生效。观察约 1 个月
    # （20 个交易日）后由用户本人决定是否切换，见设计文档第 10 节。
    decision_escalation_mode: Literal["shadow", "enforced"] = "shadow"

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
    def nav_trend_days(self) -> int:
        """Deprecated: 旧 env FUND_AI_NAV_TREND_DAYS 仍兼容，映射到 nav_cache_pull_days。"""
        return self.nav_cache_pull_days

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
    def resolved_cors_origin_regex(self) -> str | None:
        explicit = (self.cors_origin_regex or "").strip()
        if explicit:
            return explicit
        if self.cloudbase_env_id:
            return r"https://[\w-]+\.webapps\.tcloudbase\.com"
        return None

    @property
    def uses_mysql(self) -> bool:
        return bool(self.database_url and self.database_url.startswith("mysql"))

    @property
    def resolved_holdings_memory_cache_enabled(self) -> bool:
        if self.holdings_memory_cache_enabled is not None:
            return self.holdings_memory_cache_enabled
        return not self.uses_mysql


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_risk_free_rate() -> float:
    """年化无风险利率（小数）。默认 2%，可经 FUND_AI_RISK_FREE_RATE 覆盖。"""
    return get_settings().risk_free_rate


def refresh_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
