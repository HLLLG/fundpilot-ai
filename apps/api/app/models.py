from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator


TransactionDirection = Literal["buy", "sell"]
TransactionStatus = Literal["pending", "confirmed", "superseded", "skipped"]
TransactionSharesSource = Literal["user_confirmed", "derived_amount_nav"]

Action = Literal["watch", "pause_add", "staggered_add", "risk_review"]
RiskLevel = Literal["low", "medium", "high"]
DecisionStyle = Literal["conservative", "tactical", "aggressive"]
InvestmentPreset = Literal["conservative_hold", "aggressive_swing"]
SwingMonitorScope = Literal["holdings", "full_market", "both"]
SwingAlertType = Literal["take_profit", "dip_buy", "pullback", "sector_dip"]


class Holding(BaseModel):
    fund_code: str = Field(..., min_length=6, max_length=6)
    fund_name: str
    holding_amount: float = Field(..., ge=0)
    return_percent: float = 0
    daily_profit: float | None = None
    daily_return_percent: float | None = None
    holding_profit: float | None = None
    holding_return_percent: float | None = None
    sector_name: str | None = None
    sector_return_percent: float | None = None
    sector_return_percent_source: SectorReturnSource | None = None
    daily_return_percent_source: DailyReturnSource | None = None
    yesterday_profit: float | None = None
    intraday_index_name: str | None = None
    user_note: str | None = None
    amount_includes_today: bool | None = None
    settled_holding_amount: float | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_official_nav_sector(cls, data: Any) -> Any:
        from app.services.holding_migration import migrate_legacy_holding_payload

        return migrate_legacy_holding_payload(data)


class InvestorProfile(BaseModel):
    style: str = "稳健"
    horizon: str = "半年到一年"
    max_drawdown_percent: float = 8
    concentration_limit_percent: float = 35
    expected_investment_amount: float | None = None
    prefer_dca: bool = True
    avoid_chasing: bool = True
    decision_style: DecisionStyle = "conservative"
    investment_preset: InvestmentPreset = "conservative_hold"
    round_trip_fee_percent: float = 1.5
    min_net_profit_percent: float = 1.0
    hold_days_target: int = 7
    swing_alerts_enabled: bool = False
    swing_monitor_scope: SwingMonitorScope = "both"


class RiskAlert(BaseModel):
    code: str
    severity: RiskLevel
    message: str
    evidence: str


class RiskAssessment(BaseModel):
    level: RiskLevel
    suggested_action: Action
    weighted_return_percent: float
    alerts: list[RiskAlert]


AnalysisMode = Literal["fast", "deep"]
DailyProfitSource = Literal["settled", "penetration_estimate"]
SectorReturnSource = Literal["realtime", "closing_estimate"]
DailyReturnSource = Literal["sector_estimate", "official_nav", "pending_accrual"]
DataSourceType = Literal["first_party", "official", "third_party", "derived", "user_input"]
DataFreshness = Literal["fresh", "aging", "stale", "unknown", "unavailable"]
DataConfidence = Literal["high", "medium", "low", "none"]


class DataEvidence(BaseModel):
    """A point-in-time provenance envelope for one decision fact."""

    fact_id: str
    source: str
    source_type: DataSourceType
    as_of_date: str | None = None
    available_at: datetime | None = None
    fetched_at: datetime
    freshness: DataFreshness = "unknown"
    confidence: DataConfidence = "none"
    is_estimate: bool = False


class AnalysisRequest(BaseModel):
    holdings: list[Holding]
    profile: InvestorProfile = Field(default_factory=InvestorProfile)
    ocr_text: str | None = None
    analysis_mode: AnalysisMode = "deep"
    system_role_prompt: str | None = Field(default=None, max_length=4000)
    # Public opt-in for an explicitly degraded run. The server always overwrites the
    # excluded context below, so clients cannot forge provenance metadata.
    allow_stale_portfolio_snapshot: bool = False
    portfolio_snapshot_context: dict[str, Any] | None = Field(default=None, exclude=True)


class StreamFollowupRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class AnalysisPromptSaveRequest(BaseModel):
    role_prompt: str | None = Field(default=None, max_length=4000)


class AllocatePenetrationRequest(BaseModel):
    holdings: list[Holding] = Field(min_length=1)
    account_daily_profit: float
    account_daily_profit_source: DailyProfitSource | None = "penetration_estimate"


class MarketItem(BaseModel):
    topic: str
    query: str
    source: str
    note: str


class NewsItem(BaseModel):
    topic: str
    title: str
    published_at: str | None = None
    source: str | None = None
    url: str | None = None
    snippet: str | None = None
    is_today: bool = False
    related_topics: list[str] = Field(default_factory=list)


NewsSentiment = Literal["bullish", "bearish", "neutral"]


class TopicBriefPoint(BaseModel):
    headline: str
    sentiment: NewsSentiment = "neutral"
    is_today: bool = False
    source_titles: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class TopicBrief(BaseModel):
    topic: str
    summary: str
    points: list[TopicBriefPoint] = Field(default_factory=list)
    news_count: int = 0
    summarized_at: datetime | None = None
    provider: str = "deepseek-flash"


class FundProfile(BaseModel):
    fund_code: str = Field(..., min_length=6, max_length=6)
    fund_name: str
    aliases: list[str] = Field(default_factory=list)
    holding_amount: float | None = None
    settled_holding_amount: float | None = None
    holding_shares: float | None = None
    position_percent: float | None = None
    holding_profit: float | None = None
    holding_return_percent: float | None = None
    holding_cost: float | None = None
    daily_profit: float | None = None
    yesterday_profit: float | None = None
    holding_days: int | None = None
    holding_days_as_of: str | None = None
    first_purchase_date: str | None = None
    first_seen_date: str | None = None
    profit_accrual_deferred_until: str | None = None
    shares_baseline_date: str | None = None
    profit_settled_trade_date: str | None = None
    sector_name: str | None = None
    sector_return_percent: float | None = None
    intraday_index_name: str | None = None
    source: str = "alipay-overview"
    is_provisional: bool = False


class ParsedTransaction(BaseModel):
    direction: TransactionDirection
    fund_name: str
    fund_code: str | None = None
    amount_yuan: float
    trade_time: str            # "YYYY-MM-DD HH:MM:SS"
    confirm_date: str | None = None   # ISO date
    in_progress: bool = False
    # SingleFundTransactionModal 输入的是用户已在原平台确认的实际份额。
    # 旧 OCR 请求没有这两个字段，继续兼容并在确认时降级为 amount/nav 推算。
    confirmed_shares: float | None = Field(default=None, gt=0)
    fee_yuan: float | None = Field(default=None, ge=0)

    @field_validator("fund_code")
    @classmethod
    def normalize_fund_code(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        if not text.isdigit() or len(text) > 6:
            raise ValueError("fund_code 必须是最多六位数字")
        return text.zfill(6)

    @field_validator("trade_time")
    @classmethod
    def validate_trade_time(cls, value: str) -> str:
        text = str(value or "").strip().replace("/", "-").replace("T", " ")
        text = " ".join(text.split())
        if not text:
            raise ValueError("trade_time 不能为空")
        parsed: datetime | None = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError("trade_time 必须是有效的 ISO 日期或时间") from exc
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    @field_validator("confirm_date")
    @classmethod
    def validate_confirm_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError("confirm_date 必须是 YYYY-MM-DD") from exc


class FundTransaction(BaseModel):
    id: str
    fund_code: str | None = None
    fund_name: str
    direction: TransactionDirection
    amount_yuan: float
    trade_time: str
    confirm_date: str
    status: TransactionStatus = "pending"
    shares_delta: float | None = None
    nav_on_confirm: float | None = None
    confirmed_shares: float | None = Field(default=None, gt=0)
    fee_yuan: float | None = Field(default=None, ge=0)
    shares_source: TransactionSharesSource | None = None
    in_progress: bool = False
    confirmed_at: str | None = None
    dedup_key: str
    created_at: str


class ApplyTransactionsRequest(BaseModel):
    transactions: list[ParsedTransaction] = Field(default_factory=list)


class LedgerBaselinePositionInput(BaseModel):
    fund_code: str = Field(..., min_length=6, max_length=6)
    confirmed_shares: float = Field(..., gt=0)
    cost_basis_total_yuan: float | None = Field(default=None, ge=0)


class ConfirmPortfolioLedgerBaselineRequest(BaseModel):
    as_of_date: date
    cash_balance_yuan: float | None = Field(default=None, ge=0)
    positions: list[LedgerBaselinePositionInput] = Field(min_length=1)


class PortfolioSummary(BaseModel):
    total_assets: float | None = None
    daily_profit: float | None = None
    daily_return_percent: float | None = None
    daily_profit_source: DailyProfitSource | None = None
    holding_count: int = 0
    updated_at: datetime | None = None


class PortfolioDailySnapshot(BaseModel):
    snapshot_date: str
    total_assets: float | None = None
    daily_profit: float | None = None
    daily_return_percent: float | None = None
    holdings: list[dict] = Field(default_factory=list)
    captured_at: datetime | None = None


class HoldingFieldWarning(BaseModel):
    index: int
    field: str
    code: str
    message: str
    severity: str = "warn"


class HoldingListDiff(BaseModel):
    fund_code: str
    fund_name: str
    change_type: str
    index: int | None = None
    messages: list[str] = Field(default_factory=list)


class ProfileSyncResult(BaseModel):
    updated: int = 0
    created: int = 0


class FundNavPoint(BaseModel):
    date: str
    nav: float
    daily_return_percent: float | None = None


class FundNavHistory(BaseModel):
    fund_code: str
    fund_name: str
    source: str
    points: list[FundNavPoint] = Field(default_factory=list)
    latest_nav: float | None = None
    latest_date: str | None = None
    period_change_percent: float | None = None
    note: str | None = None


class FundSnapshot(BaseModel):
    fund_code: str
    fund_name: str
    latest_nav: float | None = None
    nav_date: str | None = None
    source: str
    note: str | None = None
    fund_type: str | None = None
    management_fee: str | None = None
    fund_scale_yi: float | None = None
    fund_scale_source: str | None = None
    fund_scale_as_of: str | None = None
    return_1y_percent: float | None = None
    max_drawdown_1y_percent: float | None = None


class FundRecommendation(BaseModel):
    fund_code: str
    fund_name: str
    action: str
    amount_yuan: float | None = None
    amount_note: str | None = None
    news_bullish: list[str] = Field(default_factory=list)
    news_bearish: list[str] = Field(default_factory=list)
    points: list[str] = Field(default_factory=list)
    # 2026-07 日报升级新增：向荐基（DiscoveryRecommendation）对齐的结构化决策字段。
    # 全部带默认值以兼容历史报告/离线兜底路径（不产出这些字段时不影响解析与展示）。
    confidence: str = "中"
    hold_horizon: str = ""
    risks: list[str] = Field(default_factory=list)
    decision_path: str = ""
    sector_evidence: list[str] = Field(default_factory=list)
    fund_evidence: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    # Server-owned daily execution facts. Defaults preserve historical reports;
    # the final guard overwrites any same-named fields from model output.
    tradeability: dict[str, Any] = Field(default_factory=dict)
    transaction_execution: dict[str, Any] = Field(default_factory=dict)
    suggested_position_change_percent: float | None = None
    suggested_position_change_basis: str = ""


class Report(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str
    risk: RiskAssessment
    holdings: list[Holding]
    snapshots: list[FundSnapshot] = Field(default_factory=list)
    market_context: list[MarketItem] = Field(default_factory=list)
    market_news: list[NewsItem] = Field(default_factory=list)
    topic_briefs: list[TopicBrief] = Field(default_factory=list)
    fund_recommendations: list[FundRecommendation] = Field(default_factory=list)
    summary: str
    recommendations: list[str]
    caveats: list[str]
    provider: str = "offline"
    analysis_facts: dict = Field(default_factory=dict)
    decision_contract: dict[str, Any] = Field(default_factory=dict)
    decision_events: list[dict[str, Any]] = Field(default_factory=list)


ChatRole = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    report_id: str
    role: ChatRole
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReportChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    chat_mode: AnalysisMode = "fast"


class DiscoveryRecommendation(BaseModel):
    fund_code: str
    fund_name: str
    sector_name: str
    action: str
    suggested_amount_yuan: float | None = None
    amount_note: str | None = None
    hold_horizon: str = ""
    confidence: str = "中"
    points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    news_bullish: list[str] = Field(default_factory=list)
    decision_path: str = ""
    sector_evidence: list[str] = Field(default_factory=list)
    fund_evidence: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    # 服务端确定性附加；不信任 LLM 草案里的同名字段，最终由候选事实与金额门禁覆盖。
    tradeability: dict[str, Any] = Field(default_factory=dict)
    cost_assessment: dict[str, Any] = Field(default_factory=dict)
    allocation: dict[str, Any] = Field(default_factory=dict)
    suggested_position_change_percent: float | None = None
    suggested_position_change_basis: str = ""


class EliminatedCandidate(BaseModel):
    fund_code: str
    fund_name: str
    sector_name: str = ""
    reasons: list[str] = Field(default_factory=list)
    basis: str = ""


class FundDiscoveryReport(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str
    summary: str = ""
    market_view: str = ""
    focus_sectors: list[str] = Field(default_factory=list)
    target_sectors: list[str] = Field(default_factory=list)
    candidate_pool: list[dict] = Field(default_factory=list)
    recommendations: list[DiscoveryRecommendation] = Field(default_factory=list)
    allocation_plan: dict[str, Any] = Field(default_factory=dict)
    eliminated_candidates: list[EliminatedCandidate] = Field(default_factory=list)
    discovery_facts: dict = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    provider: str = "offline"
    analysis_mode: AnalysisMode = "deep"
    decision_contract: dict[str, Any] = Field(default_factory=dict)
    decision_events: list[dict[str, Any]] = Field(default_factory=list)


FundTypePreference = Literal["any", "etf_link", "no_c_class"]
SelectionStrategy = Literal["balanced", "with_new_issue"]
DiscoveryScanMode = Literal["full_market", "portfolio_gap"]


class DiscoveryPromptSaveRequest(BaseModel):
    role_prompt: str | None = Field(default=None, max_length=4000)


class DiscoveryRequest(BaseModel):
    profile: InvestorProfile
    analysis_mode: AnalysisMode = "deep"
    focus_sectors: list[str] = Field(default_factory=list, max_length=3)
    budget_yuan: float | None = None
    holdings: list[Holding] = Field(default_factory=list)
    fund_type_preference: FundTypePreference = "any"
    selection_strategy: SelectionStrategy = "balanced"
    scan_mode: DiscoveryScanMode = "full_market"
    system_role_prompt: str | None = Field(default=None, max_length=4000)
    allow_stale_portfolio_snapshot: bool = False
    portfolio_snapshot_context: dict[str, Any] | None = Field(default=None, exclude=True)


class DiscoveryChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    discovery_report_id: str
    role: ChatRole
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiscoveryChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    chat_mode: AnalysisMode = "fast"


SectorQuoteSource = Literal["live", "ocr", "manual"]
SectorQuoteConfidence = Literal["high", "medium", "low", "none"]
SectorSourceType = Literal["index", "concept", "industry"]


class SectorMappingCandidate(BaseModel):
    source_type: SectorSourceType
    source_name: str
    change_percent: float
    source_code: str | None = None


class SectorQuoteMeta(BaseModel):
    source: SectorQuoteSource = "ocr"
    provider: str = "eastmoney-akshare"
    confidence: SectorQuoteConfidence = "none"
    matched_name: str | None = None
    source_type: SectorSourceType | None = None
    source_code: str | None = None
    fetched_at: datetime | None = None
    previous_percent: float | None = None
    delta_vs_previous: float | None = None
    message: str | None = None


class HoldingDetailRequest(BaseModel):
    holdings: list[Holding] = Field(min_length=1)
    index: int = Field(ge=0)
    portfolio_summary: PortfolioSummary | None = None
    sector_quote_meta: SectorQuoteMeta | None = None


class HoldingDetailResponse(BaseModel):
    index: int
    holding: Holding
    holding_shares: float | None = None
    holding_cost: float | None = None
    yesterday_profit: float | None = None
    holding_days: int | None = None
    first_purchase_date: str | None = None
    latest_nav: float | None = None
    nav_date: str | None = None
    year_return_percent: float | None = None
    fund_code_resolved: bool = False
    fund_code_source: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)


class UpdateFundProfileRequest(BaseModel):
    first_purchase_date: str | None = None
    fund_code: str | None = None
    fund_name: str | None = None


class AdjustHoldingRequest(BaseModel):
    settled_holding_amount: float | None = Field(default=None, ge=0)
    holding_profit: float | None = None
    holding_return_percent: float | None = None


class ApplyHoldingsRequest(BaseModel):
    holdings: list[Holding] = Field(min_length=1)


class RefreshSectorQuotesRequest(BaseModel):
    holdings: list[Holding] = Field(min_length=1)
    force_refresh: bool = False
    budget: Literal["fast", "accurate"] = "fast"


class SwingAlertItem(BaseModel):
    alert_key: str
    alert_type: SwingAlertType
    title: str
    message: str
    priority: Literal["high", "medium"] = "medium"
    fund_code: str | None = None
    fund_name: str | None = None
    sector_label: str | None = None
    is_new: bool = False


class SwingAlertEvaluateRequest(BaseModel):
    holdings: list[Holding] = Field(default_factory=list)
    profile: InvestorProfile = Field(default_factory=InvestorProfile)
    monitor_scope: SwingMonitorScope | None = None


class SwingAlertEvaluateResponse(BaseModel):
    trade_date: str
    session_kind: str
    alerts_enabled: bool
    items: list[SwingAlertItem] = Field(default_factory=list)
    new_count: int = 0


class SaveSectorMappingRequest(BaseModel):
    holdings: list[Holding] = Field(min_length=1)
    index: int = Field(ge=0)
    source_type: SectorSourceType
    source_name: str
    source_code: str | None = None


# --- 美股概览（market Tab · 美股子 Tab）---------------------------------------
# 数据源状态：ok=本次真实采集成功；stale=采集失败但沿用上次真实缓存值；
# unavailable=无可用数据（数值字段一律为 None，禁止占位常量/收盘价回退）。
DataSourceStatus = Literal["ok", "stale", "unavailable"]
# 美股交易时段（America/New_York，含夏令时）。
UsSessionKind = Literal["pre_market", "regular", "after_hours", "closed"]


class UsFuturesQuote(BaseModel):
    symbol: str  # NASDAQ_FUT | SP500_FUT | DOW_FUT
    display_name: str  # 纳斯达克 / 标普500 / 道琼斯
    # status != "ok"/"stale" 时（即 unavailable）必须为 None，禁止占位值。
    last_price: float | None = None
    change_percent: float | None = None
    quote_time: str | None = None  # 数据时间戳（ISO，源采集时刻）
    quote_caliber: str | None = None  # futures_live | index_close | futures_night
    status: DataSourceStatus = "unavailable"


class UsdCnyQuote(BaseModel):
    # status == "unavailable" 时必须为 None，禁止填占位常量。
    last_price: float | None = None
    change_percent: float | None = None
    quote_time: str | None = None
    status: DataSourceStatus = "unavailable"


class QdiiPremarketItem(BaseModel):
    fund_code: str
    fund_name: str
    tracking_target: str  # 跟踪标的（如「纳斯达克100」）
    tracking_symbol: str | None = None  # 映射到期货 symbol
    # 跟踪期货不可用/无映射时必须为 None（非承诺性预估，禁止编造）。
    reference_change_percent: float | None = None
    estimate_basis: str | None = None  # 非承诺性预估说明
    estimated_at: str | None = None  # 天天基金 gztime（如有）


class UsMarketSnapshot(BaseModel):
    session_kind: UsSessionKind
    session_label: str  # 盘前交易中 / 盘中 / 盘后 / 休市
    et_date: str  # 美东日期
    updated_at: str  # 采集时刻 ISO 时间戳（需求 4.6）
    futures: list[UsFuturesQuote]  # 固定 3 条
    usd_cny: UsdCnyQuote
    qdii: list[QdiiPremarketItem]
    qdii_status: DataSourceStatus  # QDII 列表整体状态
    qdii_estimated_at: str | None = None  # 天天基金估值时间（如有）
    futures_status: DataSourceStatus  # 期货整体状态（任一可用即 ok/stale）
    forex_status: DataSourceStatus
    available: bool  # 任一数据源可用即 True
    from_cache: bool = False
    stale: bool = False
    message: str | None = None
