export type Holding = {
  fund_code: string;
  fund_name: string;
  holding_amount: number;
  return_percent: number;
  daily_profit?: number | null;
  daily_return_percent?: number | null;
  holding_profit?: number | null;
  holding_return_percent?: number | null;
  sector_name?: string | null;
  sector_return_percent?: number | null;
  sector_return_percent_source?: string | null;
  daily_return_percent_source?: string | null;
  yesterday_profit?: number | null;
  intraday_index_name?: string | null;
  user_note?: string | null;
  /** 持有金额是否已含当日涨跌（份额×净值同步后） */
  amount_includes_today?: boolean | null;
  /** 上一交易日结算持有金额（养基宝口径） */
  settled_holding_amount?: number | null;
  /** API 展示用结算金额 */
  display_holding_amount?: number | null;
  /** 后端 holding_client.serialize 写入的展示口径（优先于前端 fallback 计算） */
  estimated_holding_return_percent?: number | null;
  estimated_holding_profit?: number | null;
  holding_return_is_estimated?: boolean | null;
  estimated_daily_return_percent?: number | null;
  daily_return_is_estimated?: boolean | null;
  profit_accrual_deferred?: boolean | null;
};

export type DecisionStyle = "conservative" | "tactical" | "aggressive";
export type InvestmentPreset = "conservative_hold" | "aggressive_swing";
export type SwingMonitorScope = "holdings" | "full_market" | "both";
export type SwingAlertType = "take_profit" | "dip_buy" | "pullback" | "sector_dip";

export type InvestorProfile = {
  style: string;
  horizon: string;
  max_drawdown_percent: number;
  concentration_limit_percent: number;
  expected_investment_amount?: number | null;
  prefer_dca: boolean;
  avoid_chasing: boolean;
  decision_style?: DecisionStyle;
  investment_preset?: InvestmentPreset;
  round_trip_fee_percent?: number;
  min_net_profit_percent?: number;
  hold_days_target?: number;
  swing_alerts_enabled?: boolean;
  swing_monitor_scope?: SwingMonitorScope;
};

export type SwingAlertItem = {
  alert_key: string;
  alert_type: SwingAlertType;
  title: string;
  message: string;
  priority: "high" | "medium";
  fund_code?: string | null;
  fund_name?: string | null;
  sector_label?: string | null;
  is_new: boolean;
};

export type SwingAlertEvaluateResponse = {
  trade_date: string;
  session_kind: string;
  alerts_enabled: boolean;
  items: SwingAlertItem[];
  new_count: number;
};

export type AnalysisMode = "fast" | "deep";

export type AnalysisPromptConfig = {
  role_prompt: string;
  is_custom: boolean;
  default_role_prompt: string;
};

export type RiskAlert = {
  code: string;
  severity: "low" | "medium" | "high";
  message: string;
  evidence: string;
};

export type TopicBriefPoint = {
  headline: string;
  sentiment: "bullish" | "bearish" | "neutral";
  is_today: boolean;
  source_titles: string[];
  source_urls?: string[];
};

export type TopicBrief = {
  topic: string;
  summary: string;
  points: TopicBriefPoint[];
  news_count: number;
  summarized_at?: string | null;
  provider: string;
};

export type Report = {
  id: string;
  created_at: string;
  title: string;
  risk: {
    level: "low" | "medium" | "high";
    suggested_action: "watch" | "pause_add" | "staggered_add" | "risk_review";
    weighted_return_percent: number;
    alerts: RiskAlert[];
  };
  holdings: Holding[];
  snapshots: Array<{
    fund_code: string;
    fund_name: string;
    latest_nav?: number | null;
    nav_date?: string | null;
    source: string;
    note?: string | null;
    fund_type?: string | null;
    management_fee?: string | null;
    fund_scale_yi?: number | null;
    return_1y_percent?: number | null;
    max_drawdown_1y_percent?: number | null;
  }>;
  market_context: Array<{
    topic: string;
    query: string;
    source: string;
    note: string;
  }>;
  market_news: Array<{
    topic: string;
    title: string;
    published_at?: string | null;
    source?: string | null;
    url?: string | null;
    snippet?: string | null;
    is_today?: boolean;
  }>;
  topic_briefs?: TopicBrief[];
  fund_recommendations: Array<{
    fund_code: string;
    fund_name: string;
    action: string;
    amount_yuan?: number | null;
    amount_note?: string | null;
    news_bullish?: string[];
    news_bearish?: string[];
    points: string[];
    confidence?: string;
    hold_horizon?: string;
    risks?: string[];
    decision_path?: string;
    sector_evidence?: string[];
    fund_evidence?: string[];
    validation_notes?: string[];
    /** M2.3：系统计算的仓位调整建议（正=建议加仓、负=建议减仓，相对当前持仓金额）。 */
    suggested_position_change_percent?: number | null;
    suggested_position_change_basis?: string;
  }>;
  summary: string;
  recommendations: string[];
  caveats: string[];
  provider: string;
  analysis_facts?: Record<string, unknown>;
};

/** M2.1：双向 guard 升级判定结果（decision_guard_shared.resolve_escalation_floor 的输出）。 */
export type DecisionEscalation = {
  min_bucket: number | null;
  min_action_label: string;
  reasons: string[];
  suggested_position_change_percent: number | null;
  basis: string;
};

export type AnalysisFactsHoldingRow = {
  fund_code?: string;
  evidence?: HoldingEvidence | null;
  sector_opportunity?: SectorOpportunity | null;
  /** M1.3：该持仓板块「量价背离」信号的历史回测（结构与 SectorSignalBacktestSector 一致）。 */
  flow_divergence_backtest?: SectorSignalBacktestSector | null;
  /** M2.1：该持仓的双向 guard 升级判定（未触发时 min_bucket 为 null）。 */
  escalation?: DecisionEscalation | null;
};

export type SectorRotationFacts = {
  available: boolean;
  reason?: string | null;
  market_top: SectorOpportunity[];
};

export type ReportOutcomes = {
  has_baseline: boolean;
  message?: string;
  previous_report_id?: string;
  previous_created_at?: string;
  portfolio_return_delta?: number | null;
  portfolio_trend_summary?: string | null;
  portfolio_assets_delta_percent?: number | null;
  items: Array<{
    fund_code: string;
    fund_name: string;
    previous_action?: string;
    current_action?: string;
    holding_return_before?: number | null;
    holding_return_after?: number | null;
    holding_return_delta?: number | null;
    daily_return_before?: number | null;
    daily_return_after?: number | null;
    daily_return_delta?: number | null;
    assessment: string;
  }>;
};

export type ReversalStats = {
  reversal_count: number;
  up_then_down_count: number;
  up_then_down_conservative_aligned: number;
  up_then_down_aggressive_miss: number;
  summary_line: string;
};

export type ReportWeeklyOutcomes = ReportOutcomes & {
  baseline_days?: number;
  baseline_report_id?: string;
  baseline_created_at?: string;
  summary?: string | null;
  hit_count?: number;
  miss_count?: number;
  reversal_stats?: ReversalStats;
};

export type TradingSession = {
  timezone: string;
  local_datetime: string;
  calendar_date: string;
  effective_trade_date: string;
  is_trading_day: boolean;
  session_kind:
    | "non_trading_day"
    | "trading_day_pre_open"
    | "trading_day_intraday"
    | "trading_day_pre_close"
    | "trading_day_after_close";
  market_open_time: string;
  minutes_to_close?: number | null;
  decision_window: string;
  market_close_time: string;
};

export type RebalanceSimulation = {
  assumption: string;
  current_total: number;
  simulated_total: number;
  concentration_limit_percent: number;
  warnings: string[];
  rows: Array<{
    fund_code: string;
    fund_name: string;
    action: string;
    current_amount: number;
    delta_yuan: number;
    simulated_amount: number;
    current_weight_percent: number;
    simulated_weight_percent: number;
    weight_delta_percent: number;
    amount_note?: string | null;
  }>;
};

export type FundProfile = {
  fund_code: string;
  fund_name: string;
  aliases: string[];
  holding_amount?: number | null;
  holding_shares?: number | null;
  position_percent?: number | null;
  holding_profit?: number | null;
  holding_return_percent?: number | null;
  holding_cost?: number | null;
  daily_profit?: number | null;
  yesterday_profit?: number | null;
  holding_days?: number | null;
  holding_days_as_of?: string | null;
  first_purchase_date?: string | null;
  sector_name?: string | null;
  sector_return_percent?: number | null;
  intraday_index_name?: string | null;
  source: string;
  is_provisional?: boolean;
  raw_text?: string;
  upload_path?: string | null;
};

export type PortfolioSummary = {
  total_assets?: number | null;
  daily_profit?: number | null;
  daily_return_percent?: number | null;
  daily_profit_source?: "settled" | "penetration_estimate" | null;
  holding_count?: number;
  updated_at?: string | null;
  profiles?: FundProfile[];
};

export type HoldingFieldWarning = {
  index: number;
  field: string;
  code: string;
  message: string;
  severity: "error" | "warn" | "info";
};

export type PortfolioHistoryPoint = {
  date: string;
  total_assets?: number | null;
  daily_profit?: number | null;
  daily_return_percent?: number | null;
};

export type PortfolioAllocationRow = {
  fund_code: string;
  fund_name: string;
  holding_amount: number;
  weight_percent: number;
  daily_profit?: number | null;
  holding_return_percent?: number | null;
};

export type ProfitRange = "today" | "week" | "month" | "year" | "all";

export type ProfitTrendPoint = {
  time?: string;
  date?: string;
  portfolio_percent?: number | null;
  index_percent?: number | null;
};

export type ProfitTrend = {
  kind: "intraday" | "daily";
  trade_date?: string | null;
  points: ProfitTrendPoint[];
};

export type ProfitTrendFooter = {
  portfolio_return_percent?: number | null;
  index_return_percent?: number | null;
  alpha_percent?: number | null;
};

export type ProfitCalendarDay = {
  date: string;
  day: number;
  weekday: number;
  is_trading_day: boolean;
  is_today: boolean;
  is_holiday: boolean;
  is_pending_update?: boolean;
  daily_profit?: number | null;
  daily_return_percent?: number | null;
};

export type ProfitCalendar = {
  year: number;
  month: number;
  days: ProfitCalendarDay[];
  month_cumulative_profit?: number | null;
  month_index_return_percent?: number | null;
  month_cumulative_return_percent?: number | null;
};

export type DailyProfitTop5Row = {
  fund_code: string;
  fund_name: string;
  daily_profit: number;
};

export type PortfolioRiskMetrics = {
  available: boolean;
  sample_days: number;
  message?: string | null;
  annualized_return_percent?: number | null;
  annualized_volatility_percent?: number | null;
  sharpe_ratio?: number | null;
  sortino_ratio?: number | null;
  max_drawdown_percent?: number | null;
  beta?: number | null;
  alpha_percent?: number | null;
  hhi?: number | null;
  effective_holdings?: number | null;
};

export type RiskCorrelationPair = {
  code_a: string;
  code_b: string;
  name_a: string;
  name_b: string;
  corr: number;
};

export type PortfolioRiskCorrelation = {
  available: boolean;
  message?: string | null;
  sample_days: number;
  codes: string[];
  names: string[];
  matrix: Array<Array<number | null>>;
  max_pair?: RiskCorrelationPair | null;
};

export type FactorDetail = {
  raw: number | null;
  z: number | null;
  percentile: number | null;
  hint?: string | null;
};

export type FactorKey = "momentum" | "risk_adjusted" | "drawdown" | "size";

export type FundFactorScore = {
  fund_code: string;
  fund_name: string;
  in_universe: boolean;
  composite_score: number | null;
  composite_grade: "A" | "B" | "C" | "D" | null;
  factors: Record<FactorKey, FactorDetail>;
};

export type FactorReliability = { level: string; basis: string };

export type PortfolioFactorScores = {
  available: boolean;
  universe_size: number;
  message?: string | null;
  funds: FundFactorScore[];
  factor_reliability?: Record<string, FactorReliability> | null;
};

export type FactorIcStatus = {
  available: boolean;
  run_date?: string;
  generated_at?: string;
  published_at?: string | null;
  age_days?: number;
  stale?: boolean;
  stale_after_days: number;
  source: "database" | "local_file" | "unavailable";
  target_universe_size?: number | null;
  universe_size?: number | null;
  universe_mode?: string | null;
  rebalance_count?: number | null;
  factor_periods?: Record<string, number | null>;
  source_commit?: string | null;
};

export type FactorIcEvidenceStatus = {
  state: "unavailable" | "stale" | "available";
  available: boolean;
  stale?: boolean;
  run_date?: string;
  source?: "database" | "local_file" | "unavailable";
};

export type EvidenceComponent = { source: string; level: string; basis: string };

export type HoldingEvidence = {
  composite: { level: string; score: number };
  components: EvidenceComponent[];
  summary: string;
};

export type EvidenceOverview = {
  available: boolean;
  total_holdings?: number;
  covered_holdings?: number;
  count_by_level?: Record<string, number>;
  weight_by_level?: Record<string, number>;
  backed_weight_percent?: number;
  summary?: string;
};

export type PortfolioEvidenceOverview = {
  available: boolean;
  overview: EvidenceOverview;
  holdings: Array<{ fund_code: string; fund_name: string; evidence: HoldingEvidence }>;
};

export type PortfolioDashboardData = {
  summary: PortfolioSummary;
  history: PortfolioHistoryPoint[];
  allocation: PortfolioAllocationRow[];
  snapshot_count: number;
  latest_snapshot_date?: string | null;
  profiles?: FundProfile[];
  profit_range?: ProfitRange;
  profit_trend?: ProfitTrend;
  profit_trend_footer?: ProfitTrendFooter;
  profit_calendar?: ProfitCalendar;
  daily_top5?: { gainers: DailyProfitTop5Row[]; losers: DailyProfitTop5Row[] };
  risk_metrics?: PortfolioRiskMetrics;
};

export type AnalysisJob = {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  error?: string | null;
  stage?: string | null;
  stage_label?: string | null;
  analysis_mode?: AnalysisMode;
  created_at: string;
  updated_at: string;
  report?: Report;
  job_kind?: "analysis" | "discovery";
  discovery_report?: FundDiscoveryReport;
  transient_unavailable?: boolean;
};

export type DiscoveryRecommendation = {
  fund_code: string;
  fund_name: string;
  sector_name: string;
  action: string;
  suggested_amount_yuan?: number | null;
  amount_note?: string | null;
  hold_horizon?: string;
  confidence?: string;
  points?: string[];
  risks?: string[];
  news_bullish?: string[];
  target_exit_days?: number | null;
  fee_break_even_percent?: number | null;
  dip_drop_percent?: number | null;
  rebound_signals?: Array<{ id: string; label: string }>;
  decision_path?: string;
  sector_evidence?: string[];
  fund_evidence?: string[];
  validation_notes?: string[];
  /** M2.3/M4：正=建议提高买入金额权重、负=建议降低（荐基语义，与日报仓位%字段对齐）。 */
  suggested_position_change_percent?: number | null;
  suggested_position_change_basis?: string;
};

/** M4/M5：被双向 guard 剔除的候选（不会出现在 recommendations 里）。 */
export type EliminatedCandidate = {
  fund_code: string;
  fund_name: string;
  sector_name?: string;
  reasons: string[];
  basis: string;
};

export type FundTypePreference = "any" | "etf_link" | "no_c_class";

export type SelectionStrategy = "balanced" | "with_new_issue" | "dip_rebound";

export type DiscoveryScanMode = "full_market" | "portfolio_gap" | "dip_swing";

export type DiscoveryPromptConfig = {
  role_prompt: string;
  is_custom: boolean;
  default_role_prompt: string;
};

export type DiscoveryCandidatePoolItem = {
  fund_code: string;
  fund_name: string;
  sector_label?: string;
  selection_reason?: string;
  return_1y_percent?: number | null;
  return_3m_percent?: number | null;
  return_6m_percent?: number | null;
  fund_scale_yi?: number | null;
  is_new_issue?: boolean;
  max_drawdown_1y_percent?: number | null;
  fund_quality_score?: number | null;
  sector_fit_score?: number | null;
  quality_reasons?: string[];
  quality_penalties?: string[];
};

export type SectorOpportunity = {
  sector_label: string;
  track?: string | null;
  score?: number | null;
  confidence?: string | null;
  entry_hint?: string | null;
  evidence?: string[];
  penalties?: string[];
  change_1d_percent?: number | null;
  change_5d_percent?: number | null;
  today_main_force_net_yi?: number | null;
  cumulative_5d_net_yi?: number | null;
  today_available?: boolean;
  five_day_available?: boolean;
  history_point_count?: number;
  pattern_label?: string | null;
  /** false = 该方向当前不构成加仓机会，仅作方向参考（日报持仓场景会返回此状态）。 */
  opportunity_available?: boolean;
};

/** @deprecated use `SectorOpportunity`（荐基与日报共用同一套板块方向数据结构）。 */
export type DiscoverySectorOpportunity = SectorOpportunity;

export type DiscoveryOutcomeItem = {
  fund_code: string;
  fund_name: string;
  action: string;
  period_change_percent?: number | null;
  direction_aligned?: boolean;
  assessment?: string;
  hit_take_profit_within_days?: boolean | null;
};

export type DiscoveryOutcomesPayload = {
  has_data: boolean;
  days?: number;
  message: string;
  items: DiscoveryOutcomeItem[];
};

export type FundDiscoveryReport = {
  id: string;
  created_at: string;
  title: string;
  summary: string;
  market_view?: string;
  focus_sectors: string[];
  target_sectors: string[];
  candidate_pool?: DiscoveryCandidatePoolItem[];
  recommendations: DiscoveryRecommendation[];
  discovery_facts?: {
    sector_opportunities?: DiscoverySectorOpportunity[];
    market_breadth?: MarketBreadthSignal | null;
    [key: string]: unknown;
  };
  caveats: string[];
  /** M4/M5：双向 guard 因证据强烈共振剔除的候选（结构化，不必解析 caveats 文案）。 */
  eliminated_candidates?: EliminatedCandidate[];
  provider: string;
  analysis_mode?: AnalysisMode;
};

export type DiscoverySectorHeat = {
  sector_label: string;
  change_1d_percent?: number | null;
  change_5d_percent?: number | null;
  heat_score?: number | null;
};

export type MarketThemeBoardSort = "change" | "inflow";

export type MarketThemeBoardKind = "industry" | "concept" | "index";

export type MarketThemeBoardFlowTiers = {
  super_large_net_yi?: number | null;
  large_net_yi?: number | null;
  medium_net_yi?: number | null;
  small_net_yi?: number | null;
};

export type MarketThemeBoardItem = {
  sector_label: string;
  board_kind: MarketThemeBoardKind;
  change_1d_percent?: number | null;
  change_5d_percent?: number | null;
  main_force_net_yi?: number | null;
  flow_tiers?: MarketThemeBoardFlowTiers | null;
  flow_source_code?: string | null;
  held_fund_count: number;
  in_portfolio: boolean;
  rank?: number;
};

export type MarketThemeBoardResponse = {
  trade_date?: string | null;
  session_kind?: string | null;
  available: boolean;
  from_cache?: boolean;
  stale?: boolean;
  refreshed_at?: string | null;
  message?: string | null;
  sort: MarketThemeBoardSort;
  items: MarketThemeBoardItem[];
};

export type BoardFlowHistoryRange = "week" | "month";

export type BoardFlowHistoryPoint = {
  date: string;
  main_force_net_yi?: number | null;
  flow_tiers?: MarketThemeBoardFlowTiers | null;
};

export type BoardFlowHistoryResponse = {
  available: boolean;
  range: BoardFlowHistoryRange;
  sector_label?: string | null;
  board_code?: string | null;
  points: BoardFlowHistoryPoint[];
  cumulative_net_yi?: number | null;
  from_cache?: boolean;
  refreshed_at?: string | null;
  message?: string | null;
};

export type DipRadarReboundSignal = {
  id: string;
  label: string;
};

export type DipRadarItem = {
  fund_code: string;
  fund_name: string;
  sector_label: string;
  dip_drop_percent?: number | null;
  change_1d_percent?: number | null;
  rebound_score?: number | null;
  rebound_signals?: DipRadarReboundSignal[];
  rank?: number;
  historical_hint?: {
    sample_count?: number;
    sample_days?: number;
    rebound_rate_3d_percent?: number;
    note?: string;
  } | null;
};

export type DipRadarSectorLeader = {
  sector_label: string;
  avg_dip_drop_percent?: number | null;
  min_dip_drop_percent?: number | null;
  fund_count?: number;
};

export type DipRadarResponse = {
  refreshed_at?: string | null;
  trade_date?: string | null;
  lookback_days: number;
  fee_break_even_percent?: number | null;
  items: DipRadarItem[];
  sector_dip_leaders?: DipRadarSectorLeader[];
  scan_stats?: {
    rank_shortlist?: number;
    dip_threshold_percent?: number;
    lookback_days?: number;
    matches?: number;
    total_matches?: number;
    sector_filter?: string;
    nav_fallback?: string;
  } | null;
  sector_filter?: string | null;
  available: boolean;
  from_cache?: boolean;
  stale?: boolean;
  session_kind?: string | null;
  message?: string | null;
};

// --- 美股概览（市场 Tab · 美股子 Tab）-------------------------------------
// 镜像后端 Pydantic 模型（models.py）。
// 数据源状态：ok=本次真实采集；stale=采集失败但沿用上次真实缓存值；
// unavailable=无可用数据（数值字段一律为 null，禁止占位常量/收盘价回退）。
export type UsDataSourceStatus = "ok" | "stale" | "unavailable";
// 美股交易时段（America/New_York，含夏令时）。
export type UsSessionKind = "pre_market" | "regular" | "after_hours" | "closed";

export type UsFuturesQuote = {
  symbol: string; // NASDAQ_FUT | SP500_FUT | DOW_FUT
  display_name: string; // 纳斯达克 / 标普500 / 道琼斯
  /** status === "unavailable" 时为 null（禁止占位值） */
  last_price?: number | null;
  change_percent?: number | null;
  quote_time?: string | null; // 数据时间戳（ISO，源采集时刻）
  quote_caliber?: string | null; // futures_live | index_close | futures_night
  status: UsDataSourceStatus;
};

export type UsdCnyQuote = {
  /** status === "unavailable" 时为 null（禁止占位值） */
  last_price?: number | null;
  change_percent?: number | null;
  quote_time?: string | null;
  status: UsDataSourceStatus;
};

export type QdiiPremarketItem = {
  fund_code: string;
  fund_name: string;
  tracking_target: string; // 跟踪标的（如「纳斯达克100」）
  tracking_symbol?: string | null; // 映射到期货 symbol
  /** 跟踪期货不可用/无映射时为 null（非承诺性预估） */
  reference_change_percent?: number | null;
  estimate_basis?: string | null; // 非承诺性预估说明
  estimated_at?: string | null; // 天天基金 gztime（如有）
};

export type UsMarketSnapshot = {
  session_kind: UsSessionKind;
  session_label: string; // 盘前交易中 / 盘中 / 盘后 / 休市
  et_date: string; // 美东日期
  updated_at: string; // 采集时刻 ISO 时间戳
  futures: UsFuturesQuote[]; // 固定 3 条
  usd_cny: UsdCnyQuote;
  qdii: QdiiPremarketItem[];
  qdii_status: UsDataSourceStatus; // QDII 列表整体状态
  qdii_estimated_at?: string | null; // 天天基金估值时间（如有）
  futures_status: UsDataSourceStatus; // 期货整体状态（任一可用即 ok/stale）
  forex_status: UsDataSourceStatus;
  available: boolean; // 任一数据源可用即 true
  from_cache?: boolean;
  stale?: boolean;
  message?: string | null;
};

export type DiscoveryChatMessage = {
  id: string;
  discovery_report_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

export type ReportChatMessage = {
  id: string;
  report_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

export type ReportChatMode = AnalysisMode;

type ReportChatStreamEvent =
  | { type: "user_message"; message: ReportChatMessage }
  | { type: "token"; content: string }
  | { type: "status"; content: string }
  | { type: "done"; message: ReportChatMessage; chat_mode?: ReportChatMode; model?: string }
  | { type: "error"; message: string };

import { clearAccessToken, getAccessToken, type AuthSession, type AuthUser } from "@/lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

/** Merge concurrent GETs within one ownership scope, avoiding Strict Mode duplicates. */
function dedupeConcurrentGet<T>(
  requests: Map<string, Promise<T>>,
  scope: string,
  run: () => Promise<T>,
): Promise<T> {
  const inFlight = requests.get(scope);
  if (inFlight) {
    return inFlight;
  }
  const task = run();
  requests.set(scope, task);
  const clear = () => {
    if (requests.get(scope) === task) {
      requests.delete(scope);
    }
  };
  void task.then(clear, clear);
  return task;
}

function authenticatedRequestScope(): string {
  return getAccessToken() ?? "unauthenticated";
}

const investorProfileRequests = new Map<string, Promise<InvestorProfile>>();
const analysisPromptRequests = new Map<string, Promise<AnalysisPromptConfig>>();
const discoveryPromptRequests = new Map<string, Promise<DiscoveryPromptConfig>>();
const listReportsRequests = new Map<string, Promise<Report[]>>();
const listDiscoveryReportsRequests = new Map<string, Promise<FundDiscoveryReport[]>>();
const portfolioHoldingsRequests = new Map<string, Promise<PortfolioHoldingsPayload>>();
const sectorQuotesStatusRequests = new Map<string, Promise<SectorQuotesStatus>>();

export function invalidatePortfolioHoldingsRequest(): void {
  portfolioHoldingsRequests.clear();
}

export type { AuthSession, AuthUser };

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isAuthEntrypoint(url: string): boolean {
  return url.includes("/api/auth/login") || url.includes("/api/auth/register");
}

function redirectToLogin(): void {
  if (typeof window === "undefined") {
    return;
  }
  const path = window.location.pathname;
  if (path === "/login" || path === "/register") {
    return;
  }
  const redirect = encodeURIComponent(path + window.location.search);
  window.location.href = `/login?redirect=${redirect}`;
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(input, { ...init, headers });
  if (
    response.status === 401 &&
    typeof window !== "undefined" &&
    token &&
    getAccessToken() === token &&
    !isAuthEntrypoint(input)
  ) {
    clearAccessToken();
    redirectToLogin();
  }
  return response;
}

export async function registerUser(payload: {
  userAccount: string;
  password: string;
  username?: string;
}): Promise<AuthSession> {
  const response = await apiFetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join("；") || "注册失败"
          : "注册失败",
    );
  }
  return response.json();
}

export async function loginUser(payload: {
  userAccount: string;
  password: string;
}): Promise<AuthSession> {
  const response = await apiFetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : "登录失败");
  }
  return response.json();
}

export async function fetchCurrentUser(): Promise<AuthUser> {
  const response = await apiFetch(`${API_BASE}/api/auth/me`, { cache: "no-store" });
  if (!response.ok) {
    throw new ApiError("未登录", response.status);
  }
  return response.json();
}

export type SectorQuoteMeta = {
  source: "live" | "ocr" | "manual";
  provider?: string;
  confidence: "high" | "medium" | "low" | "none";
  matched_name?: string | null;
  source_type?: "index" | "concept" | "industry" | null;
  source_code?: string | null;
  fetched_at?: string | null;
  previous_percent?: number | null;
  delta_vs_previous?: number | null;
  message?: string | null;
};

export type SectorMappingCandidate = {
  source_type: "index" | "concept" | "industry";
  source_name: string;
  change_percent: number;
  source_code?: string | null;
};

export type RefreshSectorQuotesResult = {
  ok: boolean;
  message: string;
  provider_path?:
    | "eastmoney_live"
    | "relay_live"
    | "browser_live"
    | "akshare_live"
    | "fund_estimate_live"
    | "fresh_cache"
    | "stale_cache"
    | "empty"
    | "disabled";
  from_stale_cache?: boolean;
  provider_elapsed_seconds?: number;
  holdings: Holding[];
  items: Array<{
    index: number;
    fund_code: string;
    fund_name: string;
    sector_name?: string | null;
    intraday_index_name?: string | null;
    sector_quote_label?: string | null;
    sector_quote_meta: SectorQuoteMeta;
    mapping_candidates: SectorMappingCandidate[];
  }>;
  holding_warnings?: HoldingFieldWarning[];
  summary: {
    matched: number;
    unresolved: number;
    needs_mapping: number;
    estimate_fallback?: number;
    board_matched?: number;
    secid_matched?: number;
    provider_path?: RefreshSectorQuotesResult["provider_path"];
    from_stale_cache?: boolean;
  };
  fetched_at?: string;
};

export type SectorQuotesStatus = {
  enabled: boolean;
  ttl_seconds: number;
  auto_interval_seconds: number;
  auto_refresh_allowed: boolean;
  session: TradingSession;
};

export async function refreshSectorQuotes(
  holdings: Holding[],
  options?: { forceRefresh?: boolean; budget?: "fast" | "accurate" },
): Promise<RefreshSectorQuotesResult> {
  const response = await apiFetch(`${API_BASE}/api/holdings/refresh-sector-quotes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings,
      force_refresh: options?.forceRefresh ?? false,
      budget: options?.budget ?? "fast",
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function applySectorMapping(
  holdings: Holding[],
  payload: {
    index: number;
    source_type: SectorMappingCandidate["source_type"];
    source_name: string;
    source_code?: string | null;
  },
): Promise<RefreshSectorQuotesResult> {
  const response = await apiFetch(`${API_BASE}/api/sector-mappings/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings,
      index: payload.index,
      source_type: payload.source_type,
      source_name: payload.source_name,
      source_code: payload.source_code ?? null,
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchSectorQuotesStatus(): Promise<SectorQuotesStatus> {
  return dedupeConcurrentGet(sectorQuotesStatusRequests, "global", async () => {
    const response = await apiFetch(`${API_BASE}/api/sector-quotes/status`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  });
}

export type SectorIntradayPoint = {
  time: string;
  percent: number;
};

export type SectorIntradayResult = {
  points: SectorIntradayPoint[];
  note?: string | null;
  session_date?: string | null;
  /** 东财 K 线收盘涨跌幅（相对昨收），与分时 15:00 一致 */
  close_change_percent?: number | null;
  source_type: string;
  source_name: string;
};

export type HoldingDetail = {
  index: number;
  holding: Holding;
  holding_shares?: number | null;
  holding_cost?: number | null;
  yesterday_profit?: number | null;
  holding_days?: number | null;
  first_purchase_date?: string | null;
  latest_nav?: number | null;
  nav_date?: string | null;
  year_return_percent?: number | null;
  fund_code_resolved: boolean;
  fund_code_source?: string | null;
  provenance: Record<string, string>;
};

export async function fetchHoldingDetail(payload: {
  holdings: Holding[];
  index: number;
  portfolio_summary?: PortfolioSummary | null;
  sector_quote_meta?: SectorQuoteMeta | null;
}): Promise<HoldingDetail> {
  const response = await apiFetch(`${API_BASE}/api/holdings/detail`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings: payload.holdings,
      index: payload.index,
      portfolio_summary: payload.portfolio_summary ?? null,
      sector_quote_meta: payload.sector_quote_meta ?? null,
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchSectorIntraday(
  payload: {
    source_type: "index" | "concept" | "industry";
    source_name: string;
  },
  options?: { forceRefresh?: boolean },
): Promise<SectorIntradayResult> {
  const params = new URLSearchParams({
    source_type: payload.source_type,
    source_name: payload.source_name,
  });
  if (options?.forceRefresh) {
    params.set("force_refresh", "true");
  }
  const response = await apiFetch(`${API_BASE}/api/sector-quotes/intraday?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function analysisPayload(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
  analysisMode: AnalysisMode = "deep",
  systemRolePrompt?: string | null,
) {
  return {
    holdings,
    profile,
    ocr_text: ocrText,
    analysis_mode: analysisMode,
    system_role_prompt: systemRolePrompt?.trim() || null,
  };
}

export async function startAnalyzeJob(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
  analysisMode: AnalysisMode = "deep",
  systemRolePrompt?: string | null,
): Promise<string> {
  const response = await apiFetch(`${API_BASE}/api/analyze/async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(
      analysisPayload(holdings, profile, ocrText, analysisMode, systemRolePrompt),
    ),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.job_id as string;
}

export async function fetchAnalysisJob(jobId: string): Promise<AnalysisJob> {
  const response = await apiFetch(`${API_BASE}/api/jobs/${jobId}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchDiscoveryJob(jobId: string): Promise<AnalysisJob> {
  const job = await fetchAnalysisJob(jobId);
  if (job.job_kind && job.job_kind !== "discovery") {
    throw new Error("Job is not a discovery task");
  }
  return job;
}

export async function fetchDiscoverySectors(): Promise<DiscoverySectorHeat[]> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await apiFetch(`${API_BASE}/api/fund-discovery/sectors`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const body = await response.json();
    return body.sectors ?? [];
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("加载板块热度超时，请点重试");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function fetchMarketThemeBoards(options?: {
  sort?: MarketThemeBoardSort;
  forceRefresh?: boolean;
}): Promise<MarketThemeBoardResponse> {
  const params = new URLSearchParams({ sort: options?.sort ?? "change" });
  if (options?.forceRefresh) {
    params.set("force_refresh", "true");
  }
  const response = await apiFetch(`${API_BASE}/api/market/theme-boards?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchBoardFlowHistory(options: {
  sectorLabel: string;
  boardCode?: string | null;
  range?: BoardFlowHistoryRange;
  forceRefresh?: boolean;
}): Promise<BoardFlowHistoryResponse> {
  const params = new URLSearchParams({
    sector_label: options.sectorLabel,
    range: options.range ?? "week",
  });
  if (options.boardCode) {
    params.set("board_code", options.boardCode);
  }
  if (options.forceRefresh) {
    params.set("force_refresh", "true");
  }
  const response = await apiFetch(`${API_BASE}/api/market/board-flow-history?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchDipRadar(options?: {
  lookbackDays?: 3 | 5;
  sector?: string | null;
  limit?: number;
  forceRefresh?: boolean;
}): Promise<DipRadarResponse> {
  const params = new URLSearchParams({
    lookback_days: String(options?.lookbackDays ?? 5),
    limit: String(options?.limit ?? 20),
  });
  if (options?.sector) {
    params.set("sector", options.sector);
  }
  if (options?.forceRefresh) {
    params.set("force_refresh", "true");
  }
  const response = await apiFetch(`${API_BASE}/api/market/dip-radar?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchUsMarketOverview(
  forceRefresh = false,
): Promise<UsMarketSnapshot> {
  const params = new URLSearchParams();
  if (forceRefresh) {
    params.set("force_refresh", "true");
  }
  const query = params.toString();
  const url = `${API_BASE}/api/market/us-overview${query ? `?${query}` : ""}`;
  const response = await apiFetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function startDiscoveryJob(
  holdings: Holding[],
  profile: InvestorProfile,
  options?: {
    analysisMode?: AnalysisMode;
    focusSectors?: string[];
    budgetYuan?: number | null;
    fundTypePreference?: FundTypePreference;
    selectionStrategy?: SelectionStrategy;
    scanMode?: DiscoveryScanMode;
    dipLookbackDays?: number;
    dipMinDropPercent?: number;
    systemRolePrompt?: string | null;
  },
): Promise<string> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings,
      profile,
      analysis_mode: options?.analysisMode ?? "deep",
      focus_sectors: options?.focusSectors ?? [],
      budget_yuan: options?.budgetYuan ?? null,
      fund_type_preference: options?.fundTypePreference ?? "any",
      selection_strategy: options?.selectionStrategy ?? "balanced",
      scan_mode: options?.scanMode ?? "full_market",
      dip_lookback_days: options?.dipLookbackDays ?? 5,
      dip_min_drop_percent: options?.dipMinDropPercent ?? 3.0,
      system_role_prompt: options?.systemRolePrompt ?? null,
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.job_id as string;
}

export async function listDiscoveryReports(): Promise<FundDiscoveryReport[]> {
  return dedupeConcurrentGet(
    listDiscoveryReportsRequests,
    authenticatedRequestScope(),
    async () => {
    const response = await apiFetch(`${API_BASE}/api/fund-discovery/reports`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
    },
  );
}

export async function deleteDiscoveryReport(reportId: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/reports/${reportId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
}

export async function fetchDiscoveryOutcomes(
  reportId: string,
  days = 7,
): Promise<DiscoveryOutcomesPayload> {
  const response = await apiFetch(
    `${API_BASE}/api/fund-discovery/reports/${reportId}/outcomes?days=${days}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchDiscoveryPrompt(): Promise<DiscoveryPromptConfig> {
  return dedupeConcurrentGet(discoveryPromptRequests, authenticatedRequestScope(), async () => {
    const response = await apiFetch(`${API_BASE}/api/discovery-prompt`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  });
}

export async function saveDiscoveryPromptRemote(
  rolePrompt: string | null,
): Promise<DiscoveryPromptConfig> {
  const response = await apiFetch(`${API_BASE}/api/discovery-prompt`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role_prompt: rolePrompt }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchDiscoveryChatHistory(
  reportId: string,
): Promise<DiscoveryChatMessage[]> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/reports/${reportId}/chat`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.messages ?? [];
}

type DiscoveryChatStreamEvent =
  | { type: "user_message"; message: DiscoveryChatMessage }
  | { type: "token"; content: string }
  | { type: "status"; content: string }
  | { type: "done"; message: DiscoveryChatMessage; chat_mode?: AnalysisMode; model?: string }
  | { type: "error"; message: string };

export async function streamDiscoveryChat(
  reportId: string,
  message: string,
  chatMode: AnalysisMode,
  onEvent: (event: DiscoveryChatStreamEvent) => void,
): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/reports/${reportId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, chat_mode: chatMode }),
  });
  if (!response.ok || !response.body) {
    throw new Error(await response.text());
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;
      const payload = JSON.parse(line.slice(6)) as DiscoveryChatStreamEvent;
      onEvent(payload);
    }
  }
}

export async function listReports(): Promise<Report[]> {
  return dedupeConcurrentGet(listReportsRequests, authenticatedRequestScope(), async () => {
    const response = await apiFetch(`${API_BASE}/api/reports`, {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  });
}

export async function deleteReport(reportId: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
}

export async function fetchReportOutcomes(reportId: string): Promise<ReportOutcomes> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/outcomes`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchReportWeeklyOutcomes(
  reportId: string,
  days = 7,
): Promise<ReportWeeklyOutcomes> {
  const response = await apiFetch(
    `${API_BASE}/api/reports/${reportId}/outcomes-weekly?days=${days}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchTradingSession(): Promise<TradingSession> {
  const response = await apiFetch(`${API_BASE}/api/trading-session`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type NewsFreshness = {
  as_of: string;
  calendar_date: string;
  total_items: number;
  today_items: number;
  today_ratio: number;
  freshness_label: string;
  median_age_minutes: number | null;
  interpretation: string;
  has_today_signal: boolean;
};

export type NewsPreviewResponse = {
  topics: string[];
  items: Array<{
    topic: string;
    title: string;
    published_at?: string | null;
    is_today: boolean;
  }>;
  freshness: NewsFreshness;
  trading_session: TradingSession;
};

export type RecommendationAccuracyBucket = {
  decision_style: string;
  paired_count: number;
  hit_count: number;
  miss_count: number;
  hit_rate_percent: number;
  reversal?: ReversalStats & { aggressive_miss_rate_percent?: number | null };
  items?: Array<{
    fund_code?: string;
    fund_name?: string;
    previous_action?: string;
    assessment?: string;
    reversal_scenario?: string | null;
  }>;
};

export type RecommendationAccuracy = {
  has_enough_data: boolean;
  message?: string;
  paired_days?: number;
  report_count?: number;
  by_style?: Record<string, RecommendationAccuracyBucket>;
  summary_lines?: string[];
};

export async function fetchRecommendationAccuracy(
  limitReports = 30,
): Promise<RecommendationAccuracy> {
  const response = await apiFetch(
    `${API_BASE}/api/reports/recommendation-accuracy?days=${limitReports}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type SectorSignalBacktestRule = {
  rule_id: string;
  label: string;
  trigger_count: number;
  hit_count: number;
  hit_rate_percent: number | null;
  baseline_rate_percent?: number | null;
  edge_percent?: number | null;
  significant?: boolean | null;
  beats_baseline?: boolean | null;
  beats_random?: boolean | null;
  confidence?: { level: string; score: number; basis: string } | null;
};

export type SectorSignalBacktestSector = {
  sector_label: string;
  sample_days?: number;
  by_rule?: Record<string, SectorSignalBacktestRule>;
  resolved?: boolean;
  message?: string;
};

export type SectorSignalBacktest = {
  enabled?: boolean;
  has_data: boolean;
  lookback_days?: number;
  sector_count?: number;
  by_rule?: Record<string, SectorSignalBacktestRule>;
  sectors?: SectorSignalBacktestSector[];
  summary_lines?: string[];
  message?: string;
};

/** M1.1：大盘情绪温度计（`GET /api/diagnostics/market-breadth`，全用户共享）。 */
export type MarketBreadthSignal = {
  available: boolean;
  reason?: string | null;
  message?: string | null;
  stale?: boolean;
  trade_date?: string;
  breadth_percentile?: number;
  breadth_sample_days?: number;
  sentiment_level?: "冰点" | "低迷" | "中性" | "偏热" | "亢奋";
  sentiment_level_change?: number | null;
  limit_up_count?: number | null;
  limit_down_count?: number | null;
  limit_up_broken_ratio_percent?: number | null;
  max_consecutive_boards?: number | null;
  limit_pool_as_of_date?: string | null;
  limit_pool_available?: boolean;
  margin_balance_change_yi?: number | null;
  margin_scope?: string | null;
  margin_as_of_date?: string | null;
  margin_available?: boolean;
  interpretation?: string;
  basis?: string;
};

export async function fetchMarketBreadth(): Promise<MarketBreadthSignal> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/market-breadth`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

/** M6.3：灰度复盘摘要（`GET /api/diagnostics/shadow-escalation-digest`）。 */
export type ShadowEscalationOutcomeItem = {
  fund_code?: string;
  sector_label?: string | null;
  would_be_action?: string;
  actual_daily_return_percent?: number | null;
  aligned?: boolean;
};

export type ShadowEscalationDigest = {
  available: boolean;
  /** 当前 FUND_AI_DECISION_ESCALATION_MODE 取值；仅 "shadow" 时该卡片才有意义展示。 */
  escalation_mode?: "shadow" | "enforced";
  lookback_days?: number;
  report_count?: number;
  discovery_report_count?: number;
  trigger_count: number;
  by_sector?: Record<string, number>;
  by_would_be_action?: Record<string, number>;
  outcomes?: {
    verified_count: number;
    aligned_count: number;
    items: ShadowEscalationOutcomeItem[];
  };
  summary?: string;
};

export async function fetchShadowEscalationDigest(
  days = 7,
): Promise<ShadowEscalationDigest> {
  const response = await apiFetch(
    `${API_BASE}/api/diagnostics/shadow-escalation-digest?days=${days}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchSectorSignalBacktest(
  days = 120,
  sectors?: string[],
): Promise<SectorSignalBacktest> {
  const params = new URLSearchParams({ days: String(days) });
  if (sectors?.length) {
    params.set("sectors", sectors.join(","));
  }
  const response = await apiFetch(
    `${API_BASE}/api/diagnostics/sector-signal-backtest?${params.toString()}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function previewNewsForHoldings(
  holdings: Holding[],
  profile: InvestorProfile,
): Promise<NewsPreviewResponse> {
  const response = await apiFetch(`${API_BASE}/api/news/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ holdings, profile }),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchRebalanceSimulation(reportId: string): Promise<RebalanceSimulation> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/rebalance-simulation`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchReportChatHistory(reportId: string): Promise<ReportChatMessage[]> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/chat`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.messages as ReportChatMessage[];
}

function parseSsePayload(line: string): ReportChatStreamEvent | null {
  if (!line.startsWith("data: ")) {
    return null;
  }
  try {
    return JSON.parse(line.slice(6)) as ReportChatStreamEvent;
  } catch {
    return null;
  }
}

export async function streamReportChat(
  reportId: string,
  message: string,
  chatMode: ReportChatMode,
  handlers: {
    onUserMessage?: (message: ReportChatMessage) => void;
    onStatus?: (content: string) => void;
    onToken: (chunk: string) => void;
    onDone: (message: ReportChatMessage) => void;
    onError?: (message: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, chat_mode: chatMode }),
    signal,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("浏览器不支持流式响应");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      for (const line of part.split("\n")) {
        const event = parseSsePayload(line);
        if (!event) {
          continue;
        }
        if (event.type === "user_message") {
          handlers.onUserMessage?.(event.message);
        } else if (event.type === "status") {
          handlers.onStatus?.(event.content);
        } else if (event.type === "token") {
          handlers.onToken(event.content);
        } else if (event.type === "done") {
          handlers.onDone(event.message);
        } else if (event.type === "error") {
          handlers.onError?.(event.message);
        }
      }
    }
  }
}

export async function fetchReportChatMarkdown(reportId: string): Promise<string> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/chat/markdown`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.markdown as string;
}

export async function fetchReportMarkdown(reportId: string): Promise<string> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/markdown`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.markdown as string;
}

export type FundCodeResolution = {
  fund_name: string;
  fund_code: string | null;
  source: string | null;
  resolved: boolean;
};

export type OcrAmountSemantics = {
  source: string;
  holding_amount: string;
  daily_profit: string;
  note?: string;
};

export type ParseOcrUploadResult = {
  raw_text: string;
  upload_path?: string | null;
  holdings: Holding[];
  cache_hit?: boolean;
  preview?: boolean;
  ocr_source?: string;
  detail_profile?: FundProfile | null;
  fund_code_resolutions?: FundCodeResolution[];
  amount_semantics?: OcrAmountSemantics;
  trading_session?: Record<string, unknown>;
  portfolio_summary?: PortfolioSummary | null;
  holding_warnings?: HoldingFieldWarning[];
  profile_sync?: { updated: number; created: number };
  sector_refresh?: Record<string, unknown> | null;
  error?: string;
};

export async function parseOcrUpload(
  formData: FormData,
  options?: { preview?: boolean },
): Promise<ParseOcrUploadResult> {
  if (options?.preview) {
    formData.set("preview", "true");
  }
  const response = await apiFetch(`${API_BASE}/api/ocr`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function applyPortfolioHoldings(
  holdings: Holding[],
): Promise<{
  holdings: Holding[];
  portfolio_summary?: PortfolioSummary | null;
}> {
  invalidatePortfolioHoldingsRequest();
  const response = await apiFetch(`${API_BASE}/api/portfolio/apply-holdings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings,
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function deletePortfolioHolding(
  fundCode: string,
  fundName?: string,
): Promise<{
  holdings: Holding[];
  portfolio_summary?: PortfolioSummary | null;
}> {
  invalidatePortfolioHoldingsRequest();
  const params = new URLSearchParams();
  if (fundName) {
    params.set("fund_name", fundName);
  }
  const query = params.toString();
  const response = await apiFetch(
    `${API_BASE}/api/portfolio/holdings/${encodeURIComponent(fundCode)}${query ? `?${query}` : ""}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    const text = await response.text();
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) {
        throw new Error(parsed.detail);
      }
    } catch (error) {
      if (error instanceof Error && error.message !== text) {
        throw error;
      }
    }
    throw new Error(text);
  }
  invalidatePortfolioHoldingsRequest();
  return response.json();
}

export type FundSearchItem = {
  fund_code: string;
  fund_name: string;
};

export async function searchFunds(query: string, limit = 12): Promise<FundSearchItem[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  const response = await apiFetch(`${API_BASE}/api/funds/search?${params.toString()}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = (await response.json()) as { items: FundSearchItem[] };
  return body.items ?? [];
}

// --- 批量加减仓（交易记录）-------------------------------------------------
// 加仓 = buy（红），减仓 = sell（绿）。
export type TransactionDirection = "buy" | "sell";

// OCR/手工解析得到的一条待应用交易（尚未落库）。
export type ParsedTransaction = {
  direction: TransactionDirection;
  fund_name: string;
  fund_code: string | null;
  amount_yuan: number;
  trade_time: string; // "YYYY-MM-DD HH:MM:SS"
  confirm_date: string | null;
  in_progress: boolean;
};

// 已落库的交易记录（含确认份额/净值与状态）。
export type FundTransaction = {
  id: string;
  fund_code: string | null;
  fund_name: string;
  direction: TransactionDirection;
  amount_yuan: number;
  trade_time: string;
  confirm_date: string;
  status: "pending" | "confirmed" | "superseded" | "skipped";
  shares_delta: number | null;
  nav_on_confirm: number | null;
  dedup_key: string;
  created_at: string;
};

export type TransactionsOcrResult = {
  transactions: ParsedTransaction[];
  ocr_source: string;
};

export type ApplyTransactionsResult = {
  holdings: Holding[];
  inserted: number;
  skipped: number;
  pending: number;
};

export async function transactionsOcr(file: File): Promise<TransactionsOcrResult> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await apiFetch(`${API_BASE}/api/transactions/ocr`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function applyTransactions(
  transactions: ParsedTransaction[],
): Promise<ApplyTransactionsResult> {
  const response = await apiFetch(`${API_BASE}/api/transactions/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transactions }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type HoldingAdjustmentPatch = {
  settled_holding_amount?: number | null;
  holding_profit?: number | null;
  holding_return_percent?: number | null;
};

export async function adjustHolding(
  fundCode: string,
  patch: HoldingAdjustmentPatch,
): Promise<PortfolioHoldingsPayload> {
  const response = await apiFetch(
    `${API_BASE}/api/portfolio/holdings/${encodeURIComponent(fundCode)}/adjust`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function getFundTransactions(
  code: string,
): Promise<{ transactions: FundTransaction[] }> {
  const response = await apiFetch(
    `${API_BASE}/api/funds/${encodeURIComponent(code)}/transactions`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type PortfolioHoldingsPayload = {
  holdings: Holding[];
  source: "snapshot" | "profiles" | "profiles_recovered" | "empty" | "official_nav_settlement";
  snapshot_date?: string | null;
  refreshed_at?: string | null;
  portfolio_summary?: PortfolioSummary | null;
  profile_count?: number;
};

export type OfficialNavSettlementPayload = PortfolioHoldingsPayload & {
  ok: boolean;
  skipped: boolean;
  reason?: string | null;
  settlement_date?: string | null;
  updated_count?: number | null;
  session?: Record<string, unknown> | null;
};

export async function fetchPortfolioHoldings(): Promise<PortfolioHoldingsPayload> {
  return dedupeConcurrentGet(portfolioHoldingsRequests, authenticatedRequestScope(), async () => {
    const response = await apiFetch(`${API_BASE}/api/portfolio/holdings`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  });
}

export async function settleOfficialNav(): Promise<OfficialNavSettlementPayload> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/settle-official-nav`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioSummary(): Promise<PortfolioSummary> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/summary`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchInvestorProfile(): Promise<InvestorProfile> {
  return dedupeConcurrentGet(investorProfileRequests, authenticatedRequestScope(), async () => {
    const response = await apiFetch(`${API_BASE}/api/investor-profile`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  });
}

export async function saveInvestorProfileRemote(profile: InvestorProfile): Promise<InvestorProfile> {
  const response = await apiFetch(`${API_BASE}/api/investor-profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function evaluateSwingAlerts(
  holdings: Holding[],
  profile: InvestorProfile,
): Promise<SwingAlertEvaluateResponse> {
  const response = await apiFetch(`${API_BASE}/api/swing-alerts/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings,
      profile,
      monitor_scope: profile.swing_monitor_scope ?? "both",
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchAnalysisPrompt(): Promise<AnalysisPromptConfig> {
  return dedupeConcurrentGet(analysisPromptRequests, authenticatedRequestScope(), async () => {
    const response = await apiFetch(`${API_BASE}/api/analysis-prompt`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  });
}

export async function saveAnalysisPromptRemote(
  rolePrompt: string | null,
): Promise<AnalysisPromptConfig> {
  const response = await apiFetch(`${API_BASE}/api/analysis-prompt`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role_prompt: rolePrompt }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioDashboard(options?: {
  range?: ProfitRange;
  calendarYear?: number;
  calendarMonth?: number;
}): Promise<PortfolioDashboardData> {
  const params = new URLSearchParams();
  if (options?.range) {
    params.set("range", options.range);
  }
  if (options?.calendarYear) {
    params.set("calendar_year", String(options.calendarYear));
  }
  if (options?.calendarMonth) {
    params.set("calendar_month", String(options.calendarMonth));
  }
  const query = params.toString();
  const response = await apiFetch(
    `${API_BASE}/api/portfolio/dashboard${query ? `?${query}` : ""}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioRiskMetrics(): Promise<PortfolioRiskMetrics> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/risk-metrics`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioRiskCorrelation(
  lookbackDays?: number,
): Promise<PortfolioRiskCorrelation> {
  const query = lookbackDays ? `?lookback_days=${lookbackDays}` : "";
  const response = await apiFetch(`${API_BASE}/api/portfolio/risk-correlation${query}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioFactorScores(): Promise<PortfolioFactorScores> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/factor-scores`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchFactorIcStatus(): Promise<FactorIcStatus> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/factor-ic-status`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioEvidenceOverview(): Promise<PortfolioEvidenceOverview> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/evidence-overview`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function updateFundProfilePurchaseDate(
  fundCode: string,
  firstPurchaseDate: string | null,
): Promise<FundProfile> {
  return updateFundProfile(fundCode, { first_purchase_date: firstPurchaseDate });
}

export async function updateFundProfile(
  fundCode: string,
  patch: {
    first_purchase_date?: string | null;
    fund_code?: string;
    fund_name?: string;
  },
): Promise<FundProfile> {
  const response = await apiFetch(`${API_BASE}/api/fund-profiles/${encodeURIComponent(fundCode)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type FundNavPoint = {
  date: string;
  nav: number;
  daily_return_percent?: number | null;
};

export type FundNavHistory = {
  fund_code: string;
  fund_name: string;
  source: string;
  points: FundNavPoint[];
  latest_nav?: number | null;
  latest_date?: string | null;
  period_change_percent?: number | null;
  note?: string | null;
};

export async function fetchFundNavHistory(
  fundCode: string,
  days = 90,
): Promise<FundNavHistory> {
  const response = await apiFetch(
    `${API_BASE}/api/fund-profiles/${encodeURIComponent(fundCode)}/nav-history?days=${days}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type FundNavHistoryPage = {
  fund_code: string;
  fund_name: string;
  source: string;
  points: FundNavPoint[];
  has_more: boolean;
  next_before?: string | null;
  note?: string | null;
};

export async function fetchFundNavHistoryPage(
  fundCode: string,
  options?: { limit?: number; before?: string | null },
): Promise<FundNavHistoryPage> {
  const params = new URLSearchParams({
    limit: String(options?.limit ?? 30),
  });
  if (options?.before) {
    params.set("before_date", options.before);
  }
  const response = await apiFetch(
    `${API_BASE}/api/fund-profiles/${encodeURIComponent(fundCode)}/nav-history/page?${params}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type IndexDailyPoint = {
  date: string;
  close: number;
};

export type IndexDailyHistory = {
  symbol: string;
  name: string;
  source: string;
  points: IndexDailyPoint[];
  period_change_percent?: number | null;
  note?: string | null;
};

export async function fetchIndexDailyHistory(
  symbol = "000300",
  days = 252,
): Promise<IndexDailyHistory> {
  const params = new URLSearchParams({
    symbol,
    days: String(days),
  });
  const response = await apiFetch(`${API_BASE}/api/market/index-daily?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export {
  streamAnalysis,
  type FundRecommendationPartial,
  type StreamingPartialField,
  type StreamingReportEvents,
  type StreamingReportState,
} from "@/lib/streamApi";
