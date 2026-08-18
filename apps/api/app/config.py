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
    # 可选：正则匹配额外 Origin（生产同源部署通常不需要）
    cors_origin_regex: str | None = None
    db_path: Path = PROJECT_ROOT / "data" / "app.db"
    # Thread-local MySQL sockets are bounded by both age and reuse count so a
    # worker cannot retain one server session indefinitely across deploys or
    # network changes.  The server-side idle timeout stays above the client
    # lifetime to make retirement deterministic on the application side.
    mysql_connection_max_lifetime_seconds: int = 1800
    mysql_connection_max_reuse_count: int = 500
    mysql_session_wait_timeout_seconds: int = 2100
    # Named locks require dedicated server sessions.  Keep their pool separate
    # from request connections and deliberately tiny per process.
    mysql_dedicated_session_pool_size: int = 2
    mysql_dedicated_session_acquire_timeout_seconds: float = 30.0
    # Shared process cache for the comparatively large profile payload set.
    # Cross-worker staleness is bounded to this short window; every write also
    # invalidates the current process immediately.
    fund_profile_cache_ttl_seconds: float = 5.0
    # Process-wide pools replace per-SSE-request fan-out pools. Analysis and
    # discovery context assembly are isolated so one pipeline cannot starve
    # the other, while the wider I/O pool absorbs bounded nested fan-out.
    sse_shared_io_workers: int = 48
    # 必须 >= 单份日报的增强项任务数（见 shared_executors.ANALYSIS_ENHANCEMENT_TASK_COUNT）：
    # 池子比任务数小时，后提交的任务会用自己的超时预算去排队，`sector_opportunity` 作为第 5 个
    # 提交项因此可能压根没启动就被判超时（2026-08-11 14:30 实测）。低于下界的取值会被
    # `get_analysis_context_executor` 抬到下界。
    sse_analysis_context_workers: int = 12
    sse_discovery_context_workers: int = 4
    # Bounded in-process telemetry avoids a new Prometheus/OTel deployment on
    # the current single-host topology. Only aggregate samples and sanitized
    # route/query fingerprints are retained.
    performance_metrics_enabled: bool = True
    performance_sample_size: int = 2048
    performance_slow_request_ms: float = 1000.0
    performance_log_sample_rate: float = 0.01
    # 运维观测（ops panel）：performance_metrics 的进程内计数器一重启就归零，
    # 也不保留任何单次报错的堆栈，所以用户反馈"出错了"时没有可查的证据。
    # 这里补一条有界的持久化通道：错误按指纹归并落库（含 traceback / JS stack），
    # 流量与响应时间按分钟落库供趋势图使用。仍然不记录请求体、查询参数、
    # Authorization 或数据库绑定参数。
    ops_error_capture_enabled: bool = True
    # 浏览器端上报入口。关掉后端点仍返回 202 但直接丢弃，便于遭遇滥用时止血。
    ops_client_error_ingest_enabled: bool = True
    ops_traffic_capture_enabled: bool = True
    ops_error_retention_days: int = 14
    ops_traffic_retention_days: int = 14
    # sector_spot_cache / news_cache / ocr_text_cache 只在读取时看 TTL，过期行不会自己消失。
    # 按 updated_at 清理，避免分时日 key、换版本前缀无限堆积。
    spot_cache_retention_days: int = 14
    # 单个指纹在同一分钟内的最大落库条数，避免一个高频异常刷爆磁盘。
    ops_error_events_per_fingerprint_per_minute: int = 20
    # 上报端点无需登录（登录页自身崩溃时也要能上报），因此必须限流。
    ops_client_error_rate_limit_per_minute: int = 60
    ops_client_error_global_rate_limit_per_minute: int = 1200
    # 落库走后台单线程，绝不占用请求线程。测试关掉它并显式调用
    # flush_ops_writes()，让断言不依赖线程调度。
    ops_writer_thread_enabled: bool = True
    # LangGraph orchestration for chat / daily / discovery. Nodes stay
    # human-owned except the narrow follow-up tool loop. Rollback:
    # FUND_AI_LANGGRAPH_ENABLED=false
    langgraph_enabled: bool = True
    langgraph_run_retention_days: int = 14
    # Production MySQL bootstrap runs behind a readiness gate so the ASGI
    # process can answer probes while schema verification is in progress.
    startup_bootstrap_background: bool = True
    upload_dir: Path = PROJECT_ROOT / "uploads"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_model_fast: str = "deepseek-v4-flash"
    deepseek_timeout_seconds: float = 300
    # End-to-end provider wall-clock budget and first-response watchdog. Set
    # either to ``0`` only as an explicit rollback.
    deepseek_request_budget_seconds: float = 180
    deepseek_first_byte_timeout_seconds: float = 60
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
    # Generic AkShare scripts reuse isolated long-lived child processes so the
    # API never imports AkShare/py_mini_racer while avoiding repeated imports.
    # Set size=0 for the one-shot rollback path.
    akshare_worker_pool_size: int = 2
    akshare_worker_max_tasks: int = 50
    akshare_worker_max_lifetime_seconds: int = 1800
    akshare_worker_acquire_timeout_seconds: float = 10.0
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
    # 截图识别只有云端 qwen-vl-ocr 一条路（本地 PaddleOCR 兜底已删除：冷加载 + CPU 推理
    # 比云端慢一个数量级，却在云端出错时静默接管，把一次报错变成一次 60s 超时）。
    vlm_ocr_api_key: str | None = None
    vlm_ocr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vlm_ocr_model: str = "qwen-vl-ocr"
    vlm_ocr_timeout_seconds: float = 15.0
    # qwen-vl-ocr 图像缩放：min/max_pixels 作为 image_url 同级字段传入（每 1024 像素≈1 图像 token）
    vlm_ocr_min_pixels: int = 3072
    # 2.0M 像素是实测的安全下界：长截图（12 只基金 / 1087x3355）在 2.0M 仍 12/12 全对，
    # 降到 1.2M 就开始认错字（鑫科技→华融科技、择时→时）。再往上只增 token 不增准确率。
    vlm_ocr_max_pixels: int = 2_000_000
    # 上传前压缩（best-effort）：转 JPEG 减小上传体积/延迟；token 由 max_pixels 控制，与文件体积无关
    vlm_ocr_compress_enabled: bool = True
    vlm_ocr_jpeg_quality: int = 80
    vlm_ocr_max_image_side: int = 1600
    jwt_secret: str = "fundpilot-dev-jwt-secret-change-me-32chars"
    jwt_access_expire_minutes: int = 43_200  # 30 days
    database_url: str | None = None
    # ``all`` keeps the one-command local developer experience. Production
    # runs API request workers with ``api`` and one dedicated container with
    # ``worker`` so long-lived jobs are not duplicated by Uvicorn processes.
    runtime_role: Literal["all", "api", "worker"] = "all"
    background_worker_lock_timeout_seconds: float = 5.0
    background_worker_retry_seconds: float = 5.0
    background_worker_heartbeat_interval_seconds: float = 10.0
    background_worker_heartbeat_stale_seconds: float = 45.0
    async_job_max_workers: int = 2
    async_job_queue_capacity: int = 8
    async_job_heartbeat_interval_seconds: float = 15.0
    async_job_stale_seconds: float = 900.0
    async_job_retry_after_seconds: int = 5
    # Long-running SSE work is admitted before the response starts. ``0``
    # disables the gate for emergency rollback; production defaults to four
    # concurrent analysis/chat streams per Uvicorn process.
    sse_max_concurrent_per_process: int = 4
    sse_retry_after_seconds: int = 5
    stream_session_ttl_seconds: int = 7_200
    eastmoney_call_deadline_seconds: float = 30
    eastmoney_max_concurrency: int = 8
    eastmoney_acquire_timeout_seconds: float = 5
    # When another pipeline is already using or waiting for Eastmoney, wait
    # longer than the solo acquire timeout so a busy peer can release slots
    # instead of the new stream failing locally with PoolTimeout.
    eastmoney_fair_acquire_timeout_seconds: float = 15
    # Reserved floors while a peer lane is active or waiting. A lone
    # analysis/discovery stream can still use the full global concurrency.
    # ``0`` disables the floor for that lane.
    eastmoney_lane_floor_analysis: int = 3
    eastmoney_lane_floor_discovery: int = 3
    eastmoney_circuit_failure_threshold: int = 3
    eastmoney_circuit_cooldown_seconds: float = 15
    # Long-lived report streams are serialized by default so two SSE
    # pipelines do not both sit on a 180s DeepSeek completion. Non-stream
    # calls (news / judge / infer) use a separate, larger gate. ``0``
    # disables that gate.
    deepseek_max_concurrent_streams: int = 1
    deepseek_max_concurrent_requests: int = 3
    deepseek_stream_acquire_timeout_seconds: float = 180
    deepseek_acquire_timeout_seconds: float = 45
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
    # 方案 A 默认关闭：美股 Tab 仅展示指数 + 汇率，不拉 QDII 穿透估值
    us_market_qdii_enabled: bool = False
    # 主题板块后台刷新：盘中 20min；收盘锁价后休市不再打源。idle 间隔仍给指数/美股用
    theme_board_refresh_enabled: bool = True
    theme_board_refresh_interval_seconds: int = 1200  # 盘中/美股活跃时段每 20min
    theme_board_refresh_idle_interval_seconds: int = 10800  # 非活跃时段每 3h（兼容旧 env）
    market_shared_idle_interval_seconds: int = 10800  # 非 A 股/美股活跃时段后台刷新间隔
    # 基金涨跌分布后台预热；关闭后诊断接口只读已有缓存，不在请求内同步打源
    fund_return_distribution_refresh_enabled: bool = True
    # 持仓共享行情缓存：后台时钟按时段刷新；请求路径只读缓存
    holding_detail_cache_ttl_seconds: int = 300
    holding_intraday_warmup_enabled: bool = True
    # 分时：仅连续竞价时段刷新；须 ≤ 服务端 live TTL，避免详情打开打到东财
    holding_intraday_refresh_interval_seconds: int = 120
    # 净值：收盘后等待官方披露的重试间隔；休市且已覆盖上一交易日则不再拉
    holding_nav_refresh_interval_seconds: int = 900
    # 全市场基金→板块离线预计算（fund_primary_sectors_global）
    fund_primary_sector_global_enabled: bool = True
    fund_primary_sector_global_benchmark_ttl_days: int = 30
    fund_primary_sector_global_holdings_ttl_days: int = 90
    fund_primary_sector_precompute_enabled: bool = True
    # 首次覆盖未完成时连续跑批；完成后只处理到期增量。单批内部按 80 只拆分，
    # 由一个隔离子进程做有限并发，避免旧的逐只子进程链路拖慢到数月。
    fund_primary_sector_precompute_batch_size: int = 800
    fund_primary_sector_precompute_profile_chunk_size: int = 80
    # 持仓核验比 profile 拉取昂贵，使用小批量 + 有界并发持续排空，候选池优先队列
    # 仍会在每轮之前处理。未通过 PIT/覆盖率/集中度门槛的结果只记 research_only。
    fund_primary_sector_precompute_holdings_batch_size: int = 32
    fund_primary_sector_precompute_holdings_workers: int = 4
    fund_primary_sector_precompute_holdings_backfill_pause_seconds: int = 30
    fund_primary_sector_precompute_interval_hours: int = 6
    fund_primary_sector_precompute_startup_delay_seconds: int = 60
    fund_primary_sector_precompute_backfill_pause_seconds: int = 5
    fund_primary_sector_precompute_unavailable_retry_hours: int = 6
    fund_primary_sector_precompute_pending_retry_days: int = 14
    fund_primary_sector_precompute_research_retry_days: int = 30
    fund_primary_sector_precompute_unmapped_retry_days: int = 30
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
    # M6：双向 guard 生效开关。灰度观察期已于 2026-08 结束，默认切到 enforced。
    # enforced 同时打开三件事（不只是"动作会被改写"）：
    #   1. M2.1/M4 的升级判定真正改写最终 action/剔除候选，不再只写 validation_notes；
    #   2. analysis_facts 的 allowed_actions 放出"大幅减仓评估""清仓评估"两个强动作词，
    #      LLM 从此可以主动选它们（shadow 下模型连选项都看不到）；
    #   3. 深度模式启用二次 LLM 审校（report_judge / discovery_judge），每份报告多一次
    #      模型调用与延迟。该审校有独立预算，超时可降级；确定性 guard 始终是硬约束。
    # shadow 保留为回滚开关：升级判定照算并落 escalation_hints，但不改动作、不扩动作
    # 词表、不发二次审校。回滚只需设 FUND_AI_DECISION_ESCALATION_MODE=shadow。
    decision_escalation_mode: Literal["shadow", "enforced"] = "enforced"
    # 日报确定性动作提议（`daily_action_proposal.propose_daily_action`）。
    #
    # 这条开关决定"谁是决策来源"：
    #   enforced（默认）—— 提议生效。九道门禁全过、而结论仍停在"观察/暂停追涨"这类被动
    #                     动作时，系统把它抬到"分批加仓"（比例仍由服务端标定档位算）。
    #                     它只让被动结论变积极：绝不覆盖任何风险动作（减仓/大幅减仓/清仓/
    #                     风控复核），也绝不绕过动作词表、仓位比例与交易门禁——提升点刻意
    #                     排在这三者之前，既有 clamp 链继续行使否决权。
    #   shadow          —— 回滚用。提议照算但只写进 validation_notes，最终 action 仍以
    #                     LLM 草案为输入，与接入前行为完全一致。
    #
    # 分歧留痕：analysis_facts.daily_action_proposal（mode / divergence_count / by_fund）。
    # 回滚：FUND_AI_DAILY_ACTION_PROPOSAL_MODE=shadow
    daily_action_proposal_mode: Literal["shadow", "enforced"] = "enforced"

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
        return explicit or None

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
