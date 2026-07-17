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
    tradeability?: FundTradeability;
    transaction_execution?: HoldingTransactionExecution;
    /** M2.3：系统计算的仓位调整建议（正=建议加仓、负=建议减仓，相对当前持仓金额）。 */
    suggested_position_change_percent?: number | null;
    suggested_position_change_basis?: string;
  }>;
  summary: string;
  recommendations: string[];
  caveats: string[];
  provider: string;
  analysis_facts?: {
    [key: string]: unknown;
  };
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
  tradeability?: FundTradeability;
  transaction_execution?: HoldingTransactionExecution;
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

export type OutcomeHorizonStats = {
  horizon_trading_days: number;
  eligible_count: number;
  mature_count: number;
  skipped_count: number;
  hit_count: number;
  miss_count: number;
  hit_rate_percent: number | null;
  coverage_percent: number | null;
  metric_contract_version?: string;
  metrics?: OutcomeMetricSummary;
  gross_direction?: OutcomeMetricStats;
  positive_net_return?: OutcomeMetricStats;
  gross_excess?: OutcomeMetricStats;
  net_excess?: OutcomeMetricStats;
};

export type OutcomeMetricName =
  | "gross_direction"
  | "positive_net_return"
  | "gross_excess"
  | "net_excess";

export type OutcomeMetricResult = {
  eligible: boolean;
  mature: boolean;
  value_percent: number | null;
  hit: boolean | null;
  unavailable_reason?: string | null;
  metadata?: Record<string, unknown>;
};

export type OutcomeMetricStats = {
  eligible_count: number;
  mature_count: number;
  unavailable_count: number;
  hit_count: number;
  miss_count: number;
  coverage_percent: number | null;
  hit_rate_percent: number | null;
};

export type OutcomeMetricSummary = Partial<Record<OutcomeMetricName, OutcomeMetricStats>>;
export type OutcomeMetricResults = Partial<Record<OutcomeMetricName, OutcomeMetricResult>>;

export type OutcomePathMetrics = {
  schema_version?: string;
  available: boolean;
  basis?: string;
  sample_days?: number;
  max_adverse_excursion_percent?: number | null;
  max_favorable_excursion_percent?: number | null;
  max_drawdown_percent?: number | null;
  daily_cvar_95?: {
    available: boolean;
    value_percent?: number | null;
    confidence_level?: number;
    unavailable_reason?: string | null;
  };
  unavailable_reason?: string | null;
};

export type NoActionCounterfactual = {
  schema_version?: string;
  available: boolean;
  comparator?: "no_action" | string;
  incremental_value_add_percent?: number | null;
  hit?: boolean | null;
  unavailable_reason?: string | null;
};

export type FrozenFeePolicy = {
  status?: string;
  fee_source?: "user_assumption" | "unavailable" | string;
  round_trip_fee_percent?: number | null;
  fee_calculation?: string | null;
  is_actual_cost?: boolean;
  recurring_fund_expenses?: string;
};

export type OutcomeBenchmark = {
  tier?: "fund_contract_exact" | "tracked_index_exact" | "category_proxy" | "unavailable" | string;
  available?: boolean;
  formal_excess_eligible?: boolean;
  return_percent?: number | null;
  reference_return_percent?: number | null;
  reason?: string | null;
  mapping_id?: string | null;
};

export type ReportOutcomeHorizon = {
  status: "mature" | "immature" | "data_unavailable" | "observation" | "invalid" | string;
  maturity_status?: string;
  horizon_trading_days: number;
  target_nav?: number;
  target_nav_date?: string | null;
  available_forward_trading_days?: number;
  return_percent?: number;
  direction_hit?: boolean | null;
  skip_reason?: string;
  metrics?: OutcomeMetricResults;
  benchmark?: OutcomeBenchmark;
  gross_direction_return_percent?: number | null;
  gross_direction_hit?: boolean | null;
  positive_net_return_percent?: number | null;
  positive_net_return_hit?: boolean | null;
  gross_excess_return_percent?: number | null;
  gross_excess_hit?: boolean | null;
  net_excess_return_percent?: number | null;
  net_excess_hit?: boolean | null;
  path_metrics?: OutcomePathMetrics;
  no_action_counterfactual?: NoActionCounterfactual;
};

export type ReportOutcomeItem = {
  fund_code: string;
  fund_name: string;
  action?: string;
  current_action?: string;
  evaluation_class?: "bullish" | "bearish" | "observation" | "invalid" | string;
  baseline_nav?: number | null;
  baseline_nav_date?: string | null;
  by_horizon?: Record<string, ReportOutcomeHorizon>;
  fee_policy?: FrozenFeePolicy;
  benchmark?: OutcomeBenchmark;
  metric_contract_version?: string;
  assessment: string;
  // Historical adjacent-report fields remain optional for old stored responses.
  previous_action?: string;
  holding_return_before?: number | null;
  holding_return_after?: number | null;
  holding_return_delta?: number | null;
  daily_return_before?: number | null;
  daily_return_after?: number | null;
  daily_return_delta?: number | null;
};

export type ReportOutcomes = {
  schema_version?: string;
  metric_status?: string;
  metric_version?: string;
  evaluation_basis?: string;
  has_baseline: boolean;
  has_data?: boolean;
  message?: string;
  horizons?: number[];
  eligible_count?: number;
  mature_count?: number;
  skipped_count?: number;
  observation_count?: number;
  coverage_percent?: number | null;
  by_horizon?: Record<string, OutcomeHorizonStats>;
  metric_contract_version?: string;
  metrics?: OutcomeMetricSummary;
  event_contract?: {
    persistence?: string;
    decision_event_schema_version?: string;
    outcome_observation_schema_version?: string;
    metric_contract_version?: string;
  };
  previous_report_id?: string;
  previous_created_at?: string;
  portfolio_return_delta?: number | null;
  portfolio_trend_summary?: string | null;
  portfolio_assets_delta_percent?: number | null;
  items: ReportOutcomeItem[];
};

export type ReversalStats = {
  reversal_count: number;
  up_then_down_count: number;
  up_then_down_conservative_aligned: number;
  up_then_down_aggressive_miss: number;
  summary_line: string;
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
  sample_quality?: "insufficient" | "short_window" | "standard" | string;
  annualization_reliable?: boolean;
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
  peer_group?: string | null;
  peer_group_label?: string | null;
  peer_count?: number | null;
  factor_reliability?: Record<string, FactorReliability> | null;
};

export type FactorReliability = { level: string; basis: string };

export type PortfolioFactorScores = {
  available: boolean;
  universe_size: number;
  message?: string | null;
  funds: FundFactorScore[];
  factor_reliability?: Record<string, FactorReliability> | null;
  model_version?: string | null;
  reliability_scope?: "per_fund_peer_group" | "global_legacy" | string;
  ic_status?: FactorIcStatus | null;
};

export type FactorIcStatus = {
  available: boolean;
  snapshot_id?: string | null;
  schema_version?: number;
  upgrade_required?: boolean;
  expected_universe_size?: number;
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
  cohort_mode?: "current_survivors" | "point_in_time" | string | null;
  point_in_time?: {
    snapshot_id?: string | null;
    snapshot_date?: string | null;
    effective_anchor_count?: number;
    anchor_coverage_rate?: number;
    cohort_nav_coverage_rate?: number;
    publishable?: boolean;
    point_in_time_scope?: "membership_only" | "nav_observation_pit" | string;
    nav_revision_pit?: boolean;
    nav_publication_lag_trading_days?: Record<string, number>;
    execution_entry_offset_trading_days?: number;
    mature_anchor_count_by_horizon?: Record<string, number>;
  } | null;
  pit_upgrade?: {
    state?: "collecting" | "unavailable" | "active" | string;
    snapshot_count?: number;
    effective_anchor_count?: number;
    anchor_coverage_rate?: number;
    cohort_nav_coverage_rate?: number;
    reason?: string;
  } | null;
  pit_coverage?: Record<string, unknown> | null;
  source_commit?: string | null;
};

export type FactorIcEvidenceStatus = {
  state: "unavailable" | "stale" | "available";
  available: boolean;
  stale?: boolean;
  run_date?: string;
  source?: "database" | "local_file" | "unavailable";
};

export type EvidenceLevelMetric = {
  level: string;
  score?: number | null;
  basis?: string;
};

export type EvidenceCoverageMetric = {
  level: string;
  percent?: number | null;
  basis?: string;
};

export type EvidenceFreshnessMetric = {
  status: "fresh" | "stale" | "unavailable" | "unknown" | string;
  as_of?: string | null;
  basis?: string;
};

export type EvidenceComponent = {
  source: string;
  role?: "return_signal" | "risk_guard" | string;
  level: string;
  basis: string;
  reliability?: EvidenceLevelMetric;
  direction?: "positive" | "negative" | "mixed" | "neutral" | "risk" | "unknown" | string;
  effect_size?: EvidenceLevelMetric;
  coverage?: EvidenceCoverageMetric;
  freshness?: EvidenceFreshnessMetric;
};

export type HoldingEvidence = {
  schema_version?: string;
  composite: {
    level: string;
    score: number;
    reliability?: EvidenceLevelMetric;
    direction?: "positive" | "negative" | "mixed" | "neutral" | "unknown" | string;
    effect_size?: EvidenceLevelMetric;
    coverage?: EvidenceCoverageMetric;
    freshness?: EvidenceFreshnessMetric;
    positive_component_count?: number;
    negative_component_count?: number;
    neutral_component_count?: number;
    risk_guard_count?: number;
  };
  components: EvidenceComponent[];
  risk_guards?: EvidenceComponent[];
  summary: string;
};

export type EvidenceOverview = {
  available: boolean;
  total_holdings?: number;
  covered_holdings?: number;
  count_by_level?: Record<string, number>;
  weight_by_level?: Record<string, number>;
  backed_weight_percent?: number;
  direction_counts?: Record<string, number>;
  risk_guard_weight_percent?: number;
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

export type FundTradeabilityGateStatus = "eligible" | "watch_only" | "excluded" | string;

export type FundTradeabilityGate = {
  schema_version?: string;
  status?: FundTradeabilityGateStatus;
  effective_initial_min_purchase_yuan?: number | null;
  effective_additional_min_purchase_yuan?: number | null;
  /** Compatibility alias retained by older Phase B snapshots. */
  effective_min_purchase_yuan?: number | null;
  max_purchase_yuan?: number | null;
  max_purchase_unlimited?: boolean;
  max_period?: "day" | string;
  max_scope?: string;
  revalidation_required?: boolean;
  reason_codes?: string[];
};

export type FundTransactionFeeTier = {
  condition?: string;
  fee_type?: "percent" | "flat" | string;
  fee_percent?: number | null;
  flat_fee_yuan?: number | null;
  min_amount_yuan?: number | null;
  max_amount_yuan?: number | null;
  min_holding_days?: number | null;
  max_holding_days?: number | null;
  source_rate?: string;
  [key: string]: unknown;
};

export type FundTradeability = {
  schema_version?: string;
  fund_code?: string;
  data_status?: "complete" | "partial" | "stale" | "unavailable" | string;
  freshness?: "fresh" | "stale" | "unavailable" | string;
  status_checked_at?: string | null;
  purchase_status_checked_at?: string | null;
  purchase_status_freshness?: "fresh" | "stale" | "unavailable" | string;
  redemption_status_checked_at?: string | null;
  redemption_status_freshness?: "fresh" | "stale" | "unavailable" | string;
  can_purchase?: boolean | null;
  purchase_state?:
    | "open"
    | "limited"
    | "suspended"
    | "closed"
    | "subscription_period"
    | "exchange_only"
    | "unknown"
    | string;
  purchase_status?: string | null;
  redemption_state?: "open" | "suspended" | "closed" | "exchange_only" | "unknown" | string;
  redemption_status?: string | null;
  currency?: "CNY" | "unknown" | string;
  minimum_purchase_yuan?: number | null;
  minimum_initial_purchase_yuan?: number | null;
  minimum_additional_purchase_yuan?: number | null;
  minimums?: {
    initial_yuan?: number | null;
    additional_yuan?: number | null;
    status?: string;
  };
  daily_purchase_limit_yuan?: number | null;
  daily_purchase_limit_unlimited?: boolean;
  daily_purchase_limit_scope?: string;
  purchase_limit?: {
    amount_yuan?: number | null;
    kind?: "finite" | "unlimited" | "unknown" | string;
    period?: string;
    scope?: string;
  };
  tradeability_gate?: FundTradeabilityGate;
  revalidation_required?: boolean;
  next_open_date?: string | null;
  purchase_confirmation?: string | null;
  redemption_confirmation?: string | null;
  explicit_minimum_holding_days?: number | null;
  minimum_holding_period_status?: "explicit_from_fund_name" | "unverified" | string;
  listed_platform_purchase_fee_percent?: number | null;
  listed_platform_fee_semantics?: string | null;
  standard_purchase_fee_tiers?: FundTransactionFeeTier[];
  redemption_fee_tiers?: FundTransactionFeeTier[];
  sales_service_fee_annual_percent?: number | null;
  management_fee_annual_percent?: number | null;
  custody_fee_annual_percent?: number | null;
  share_class_fee_status?: string;
  fee_checked_at?: string | null;
  fee_freshness?: "fresh" | "stale" | "unavailable" | string;
  source_conflict?: boolean;
  missing_fields?: string[];
  source_ids?: string[];
  source_urls?: string[];
  checked_at?: string | null;
  effective_at?: string | null;
  instruction?: string;
};

export type HoldingTransactionExecution = {
  schema_version?: string;
  existing_holding_confirmed?: boolean;
  purchase_minimum_basis?: "existing_holding_additional_purchase" | string;
  first_or_additional_semantics?: string;
  add_status?: "eligible" | "watch_only" | string;
  add_block_reasons?: string[];
  effective_additional_min_purchase_yuan?: number | null;
  max_purchase_yuan?: number | null;
  max_purchase_unlimited?: boolean;
  redemption_status?: "eligible" | "watch_only" | string;
  redemption_block_reasons?: string[];
  acquisition_lot_status?: "unverified" | string;
  minimum_holding_period_at_lot_status?: "unverified" | string;
  redemption_fee_at_lot_age_status?: "unverified" | string;
  redemption_fee_rules_status?: "available_for_manual_review" | "unavailable" | string;
  reduction_amount_status?: "manual_review" | string;
  /** 服务端按最终减仓档位计算的核验前目标市值；不是可直接执行的订单金额。 */
  review_target_amount_yuan?: number | null;
  review_target_percent?: number | null;
  review_target_basis?: string | null;
  revalidation_required?: boolean;
  instruction?: string;
  amount_assessment?: {
    schema_version?: string;
    executable?: boolean;
    requested_amount_yuan?: number | null;
    approved_amount_yuan?: number | null;
    amount_capped_by_daily_limit?: boolean;
    minimum_additional_purchase_yuan?: number | null;
    daily_purchase_limit_yuan?: number | null;
    daily_purchase_limit_unlimited?: boolean;
    block_reasons?: string[];
  };
};

export type FundTransactionCostAssessment = {
  schema_version?: string;
  executable?: boolean;
  amount_yuan?: number | null;
  hold_horizon?: string | null;
  minimum_holding_days?: number | null;
  fund_minimum_holding_days?: number | null;
  minimum_purchase_yuan?: number | null;
  tradeability_gate?: FundTradeabilityGate;
  daily_purchase_limit_yuan?: number | null;
  daily_purchase_limit_unlimited?: boolean;
  purchase_fee_standard_upper_bound?: {
    fee_type?: "percent" | "flat" | string;
    fee_percent?: number | null;
    flat_fee_yuan?: number | null;
    fee_yuan?: number | null;
    condition?: string;
    source_rate?: string;
  } | null;
  redemption_fee_percent_at_minimum_horizon?: number | null;
  sales_service_fee_percent_for_minimum_horizon?: number | null;
  estimated_total_cost_upper_bound_percent?: number | null;
  fee_status?: "standard_upper_bound_available" | "execution_verification_required" | "unavailable" | string;
  block_reasons?: string[];
  notes?: string[];
  source_ids?: string[];
  checked_at?: string | null;
  fee_checked_at?: string | null;
  fee_freshness?: "fresh" | "stale" | "unavailable" | string;
  instruction?: string;
};

export type DiscoveryBenchmarkResearch = {
  schema_version?: string;
  mapping_id?: string | null;
  benchmark_code?: string | null;
  benchmark_name?: string | null;
  comparison_role?: "formal_excess" | "tracking_reference" | "unavailable" | string;
  formal_excess_eligible?: boolean;
  qualified?: boolean;
  contract_verification_kind?: string | null;
  available_at?: string | null;
  reason?: string | null;
  instruction?: string;
  [key: string]: unknown;
};

export type DiscoveryBenchmarkSpec = DiscoveryBenchmarkResearch & {
  tier?: "fund_contract_exact" | "tracked_index_exact" | "unavailable" | string;
  status?: string;
  benchmark_kind?: "official_contract" | "tracking_index" | string;
  completeness?: "complete" | "incomplete" | string;
  components?: Array<Record<string, unknown>>;
};

export type DiscoveryBenchmarkHorizonMetrics = {
  status?: "available" | "unavailable" | string;
  start_date?: string | null;
  end_date?: string | null;
  fund_return_percent?: number | null;
  benchmark_return_percent?: number | null;
  formal_excess_return_percent?: number | null;
  reference_difference_percent?: number | null;
  fund_max_drawdown_percent?: number | null;
  benchmark_max_drawdown_percent?: number | null;
  drawdown_advantage_percent?: number | null;
  [key: string]: unknown;
};

export type DiscoveryBenchmarkMetrics = DiscoveryBenchmarkResearch & {
  status?: "qualified" | "insufficient" | "unavailable" | string;
  descriptive_only?: boolean;
  execution_tilt_eligible?: boolean;
  effective_trade_date?: string | null;
  reason_codes?: string[];
  alignment?: {
    common_return_sample_days?: number | null;
    first_common_date?: string | null;
    last_common_date?: string | null;
    [key: string]: unknown;
  };
  horizons?: Record<string, DiscoveryBenchmarkHorizonMetrics>;
  rolling_comparison?: {
    window_days?: number | null;
    window_count?: number | null;
    formal_excess_win_rate_percent?: number | null;
    reference_outperformance_rate_percent?: number | null;
    difference_stability_percent?: number | null;
    [key: string]: unknown;
  };
  tracking_metrics?: {
    applicable?: boolean;
    available?: boolean;
    tracking_difference_percent?: number | null;
    annualized_tracking_error_percent?: number | null;
    [key: string]: unknown;
  };
};

export type DiscoveryPeerGroup = {
  schema_version?: string;
  decision_at?: string;
  group_key?: string | null;
  group_label?: string | null;
  fund_type_key?: string | null;
  asset_class?: string | null;
  management_style?: string | null;
  region?: string | null;
  bond_subtype?: string | null;
  mixed_subtype?: string | null;
  qdii_subtype?: string | null;
  qdii_region?: string | null;
  fof_subtype?: string | null;
  risk_bucket?: string | null;
  exposure_bucket?: string | null;
  reference_code?: string | null;
  classification_sources?: string[];
  classification_confidence?: "high" | "medium" | "low" | string;
  qualified?: boolean;
  reason?: string | null;
  reasons?: string[];
  warnings?: string[];
  applicable_metrics?: string[];
  benchmark?: DiscoveryBenchmarkResearch;
  [key: string]: unknown;
};

export type DiscoveryPeerRankMetric = {
  label?: string;
  orientation?: string;
  role?: "performance" | "risk" | "capacity_context_only" | string;
  applicable?: boolean;
  applicability?: "applicable" | "not_applicable" | string;
  available?: boolean;
  availability?: "available" | "unavailable" | "not_applicable" | string;
  value?: number | null;
  value_available_at?: string | null;
  value_as_of?: string | null;
  value_source?: string | null;
  independent_peer_family_count?: number;
  sample_count?: number;
  coverage_rate?: number;
  percentile?: number | null;
  qualified?: boolean;
  qualification_required?: boolean;
  reason?: string | null;
  reasons?: string[];
  peer_sample_hash?: string;
  [key: string]: unknown;
};

export type DiscoveryPeerRank = {
  schema_version?: string;
  decision_at?: string;
  target_fund_code?: string | null;
  target_family_key?: string;
  /** Compact `peer_research` snapshots expose group metadata at the top level. */
  group_key?: string | null;
  group_label?: string | null;
  classification_confidence?: "high" | "medium" | "low" | string;
  metric_registry_version?: string;
  metric_profile?: string;
  independent_peer_family_count?: number;
  peer_group?: DiscoveryPeerGroup;
  status?: "qualified" | "descriptive_only" | "insufficient" | string;
  qualified?: boolean;
  research_shadow_rerank_eligible?: boolean;
  execution_tilt_eligible?: boolean;
  execution_tilt_gate?: {
    status?: string;
    eligible?: boolean;
    required_method?: string;
    reason?: string | null;
  };
  reason?: string | null;
  reasons?: string[];
  qualification_policy?: Record<string, unknown>;
  universe?: {
    raw_member_count?: number;
    point_in_time_member_count?: number;
    membership_unavailable_or_future_count?: number;
    group_share_class_count?: number;
    independent_peer_family_count?: number;
    target_family_share_class_count_excluded?: number;
    duplicate_share_class_count?: number;
    [key: string]: unknown;
  };
  metrics?: Record<string, DiscoveryPeerRankMetric>;
  descriptive_percentile_count?: number;
  applicable_metric_count?: number;
  available_applicable_metric_count?: number;
  not_applicable_metric_count?: number;
  descriptive_performance_percentile?: number | null;
  descriptive_performance_semantics?: string;
  qualified_metric_count?: number;
  target_metric_coverage_rate?: number;
  benchmark?: DiscoveryBenchmarkResearch;
  [key: string]: unknown;
};

export type DiscoveryFutureTranche = {
  sequence?: number;
  amount_yuan?: number | null;
  revalidation_required?: boolean;
  preconditions?: string[];
  [key: string]: unknown;
};

export type DiscoveryAllocation = {
  fund_code?: string;
  sector_name?: string;
  suggested_amount_yuan?: number | null;
  amount_semantics?: "current_verified_initial_tranche" | string;
  constraint_snapshot?: {
    effective_initial_min_purchase_yuan?: number | null;
    candidate_purchase_cap_yuan?: number | null;
    amount_step_yuan?: number | null;
    [key: string]: unknown;
  };
  priority?: {
    qualified_priority_score?: number | null;
    qualified_peer_score_percentile?: number | null;
    peer_tilt_status?: string;
    risk_multiplier?: number | null;
    current_portfolio_correlation_penalty?: number | null;
    combined_weight?: number | null;
    [key: string]: unknown;
  };
  future_tranches?: DiscoveryFutureTranche[];
  revalidation_required?: boolean;
  [key: string]: unknown;
};

export type DiscoveryAllocationRiskSummary = {
  schema_version?: string;
  status?: "qualified" | "unqualified" | "risk_context_unavailable" | string;
  reason_codes?: string[];
  fallback_rule?: string | null;
  [key: string]: unknown;
};

export type DiscoveryAllocationPlan = {
  schema_version?: string;
  status?: "allocated" | "partial" | "blocked" | string;
  allocation_mode?: string;
  amount_semantics?: "current_verified_initial_tranche" | string;
  policy?: {
    decision_style?: string;
    prefer_dca?: boolean;
    nominal_current_tranche_ratio?: number;
    applied_current_tranche_ratio?: number;
    amount_step_yuan?: number;
    concentration_denominator_yuan?: number;
    concentration_limit_percent?: number;
    stable_tie_break?: string;
    candidate_order_ignored?: boolean;
    llm_amount_and_prose_ignored?: boolean;
    risk_weight_method?: string;
    [key: string]: unknown;
  };
  risk_context?: DiscoveryAllocationRiskSummary;
  budget?: {
    requested_yuan?: number | null;
    confirmed_cash_yuan?: number | null;
    spendable_yuan?: number | null;
    current_tranche_cap_yuan?: number | null;
    allocated_current_tranche_yuan?: number | null;
    [key: string]: unknown;
  };
  sector_constraints?: Array<Record<string, unknown>>;
  allocations?: DiscoveryAllocation[];
  excluded_candidates?: Array<{
    fund_code?: string;
    sector_name?: string;
    reason_codes?: string[];
    [key: string]: unknown;
  }>;
  unallocated_budget?: {
    amount_yuan?: number | null;
    current_tranche_unallocated_yuan?: number | null;
    deferred_future_tranches_yuan?: number | null;
    unavailable_due_to_cash_yuan?: number | null;
    reason_codes?: string[];
    [key: string]: unknown;
  };
  revalidation_required?: boolean;
  [key: string]: unknown;
};

export type DiscoveryRiskContext = {
  schema_version?: string;
  status?: "qualified" | "unqualified" | string;
  qualified?: boolean;
  decision_at?: string | null;
  effective_trade_date?: string | null;
  configuration?: Record<string, unknown>;
  candidate_codes?: string[];
  holding_codes?: string[];
  candidate_common_return_sample_days?: number;
  current_holdings_nav_amount_coverage_ratio?: number;
  current_holdings_nav_amount_coverage_percent?: number;
  current_holdings_covered_amount_yuan?: number;
  current_holdings_total_amount_yuan?: number;
  max_drawdown_percent_by_code?: Record<string, number>;
  covariance_by_code?: Record<string, Record<string, number>>;
  correlation_by_code?: Record<string, Record<string, number>>;
  candidate_to_current_holding_correlation_by_code?: Record<
    string,
    Record<string, number>
  >;
  positive_correlation_penalty_to_current_holdings_by_code?: Record<string, number>;
  scenario_drawdown?: {
    current_portfolio_max_drawdown_percent?: number;
    current_portfolio_return_sample_days?: number;
    equal_weight_candidate_basket_max_drawdown_percent?: number;
    equal_weight_candidate_basket_return_sample_days?: number;
    current_portfolio_basis?: string;
    [key: string]: unknown;
  };
  series_by_code?: Record<string, Record<string, unknown>>;
  reason_codes?: string[];
  snapshot_hash?: string;
  [key: string]: unknown;
};

export type DiscoveryEntryTriggerCondition = {
  metric: string;
  label: string;
  current_value?: number | null;
  operator?: "lt" | "lte" | "gt" | "gte" | "eq" | null;
  target_value?: number | null;
  unit?: string;
  current_text?: string;
  target_text?: string;
};

export type DiscoveryEntryTrigger = {
  schema_version?: string;
  status?: "waiting" | string;
  reason_code: string;
  headline: string;
  release_mode?: "any" | "all" | string;
  conditions: DiscoveryEntryTriggerCondition[];
  recheck_policy?: "next_discovery_scan" | string;
  recheck_label?: string;
};

export type DiscoveryQuantPreview = {
  schema_version?: string;
  label?: string;
  mode: "off" | "shadow" | "enforced";
  status: "eligible" | "ineligible";
  application_status: "not_applied" | "shadow_only" | "applied";
  evidence_role?: "bounded_initial_tranche_modifier_only";
  model_version?: string | null;
  cohort_mode?: string | null;
  snapshot_id?: string | null;
  data_as_of?: string | null;
  survivorship_bias?: boolean;
  confidence_cap?: string;
  peer_group?: string | null;
  preview_score?: number | null;
  qualifying_factor_keys?: string[];
  sector_rank?: number | null;
  sector_sample_size?: number | null;
  rank_scope?: string | null;
  max_adjustment_percent?: number;
  proposed_adjustment_percent?: number;
  applied_adjustment_percent?: number;
  base_amount_yuan?: number | null;
  projected_amount_yuan?: number | null;
  adjusted_amount_yuan?: number | null;
  reasons?: string[];
  guardrails?: string[];
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
  decision_path?: string;
  sector_evidence?: string[];
  fund_evidence?: string[];
  validation_notes?: string[];
  entry_trigger?: DiscoveryEntryTrigger | null;
  quant_preview?: DiscoveryQuantPreview | null;
  tradeability?: FundTradeability;
  tradeability_gate?: FundTradeabilityGate;
  cost_assessment?: FundTransactionCostAssessment;
  allocation?: DiscoveryAllocation;
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

export type DiscoveryQualityGateStatus = "eligible" | "watch_only" | "excluded";

export type DiscoveryCandidateQualityGate = {
  eligible: boolean;
  status: DiscoveryQualityGateStatus;
  reasons: string[];
  missing_fields: string[];
  coverage_percent: number;
  data_as_of?: string | null;
  profile_status?: "complete" | "partial" | "stale_fallback" | "unavailable" | string | null;
  profile_sources?: string[];
  profile_checked_at?: string | null;
  profile_stale_fields?: string[];
  tradeability_status?: string | null;
  tradeability_checked_at?: string | null;
  purchase_state?: string | null;
  tradeability_gate_status?: FundTradeabilityGateStatus | null;
};

export type DiscoveryCandidateQualitySummary = {
  eligible_count: number;
  watch_only_count: number;
  excluded_count: number;
  total_count: number;
  required_fields: string[];
  coverage_percent: number;
  missing_field_counts?: Record<string, number>;
  profile_status_counts?: Record<string, number>;
  profile_source_counts?: Record<string, number>;
};

export type DiscoveryDataEvidenceGuard = {
  execution_blocked: boolean;
  blocked_fund_codes: string[];
  reasons_by_fund?: Record<string, string[] | string>;
  quant_evidence_blocked_fund_codes?: string[];
  quant_evidence_uncovered_fund_codes?: string[];
};

export type DiscoveryDecisionActionCategory =
  | "buy"
  | "watch_only"
  | "conditional_wait"
  | "invalid";

export type DiscoveryDecisionEvent = {
  fund_code?: string | null;
  final_action?: string | null;
  action_category?: DiscoveryDecisionActionCategory | string;
  evaluation_class?: DiscoveryDecisionActionCategory | string;
  eligible?: boolean;
};

export type FundTypePreference = "any" | "etf_link" | "no_c_class";

export type SelectionStrategy = "balanced" | "with_new_issue";

export type DiscoveryScanMode = "full_market" | "portfolio_gap";
export type DiscoveryStrategy = "opportunity_first" | "risk_first";

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
  fund_scale_basis?: "nav_times_latest_shares" | "nav_times_xq_latest_shares" | string | null;
  fund_shares_yi?: number | null;
  fund_shares_basis?: "xq_latest_reported_shares" | string | null;
  fund_manager?: string | null;
  established_date?: string | null;
  nav_date?: string | null;
  profile_updated_at?: string | null;
  profile_status?: "complete" | "partial" | "stale_fallback" | "unavailable" | string | null;
  profile_sources?: string[];
  profile_stale_fields?: string[];
  is_new_issue?: boolean;
  max_drawdown_1y_percent?: number | null;
  fund_quality_score?: number | null;
  opportunity_score_20_60d?: number | null;
  opportunity_score_version?: string | null;
  sector_fit_score?: number | null;
  quality_reasons?: string[];
  quality_penalties?: string[];
  quality_gate?: DiscoveryCandidateQualityGate;
  tradeability?: FundTradeability;
  tradeability_gate?: FundTradeabilityGate;
  cost_assessment?: FundTransactionCostAssessment;
  peer_group?: DiscoveryPeerGroup;
  peer_rank?: DiscoveryPeerRank;
  /** Compact generation payload retained by some snapshots. */
  peer_research?: DiscoveryPeerRank;
  benchmark_spec?: DiscoveryBenchmarkSpec;
  benchmark_comparison?: DiscoveryBenchmarkResearch;
  /** Compact generation payload retained by some snapshots. */
  benchmark_research?: DiscoveryBenchmarkResearch;
  benchmark_metrics?: DiscoveryBenchmarkMetrics;
};

export type MainlineRegime = {
  schema_version?: string;
  policy_version?: string;
  sector_label?: string;
  as_of_trade_date?: string | null;
  status?: "forming" | "confirmed" | "crowded" | "fading" | "neutral" | "insufficient" | string;
  score?: number | null;
  confidence?: string | null;
  feature_coverage?: number | null;
  research_ranking_only?: boolean;
  execution_eligible?: boolean;
  component_scores?: Record<string, number | null>;
  risk_penalty?: number | null;
  features?: {
    change_1d_percent?: number | null;
    return_5d_percent?: number | null;
    return_10d_percent?: number | null;
    return_20d_percent?: number | null;
    return_60d_percent?: number | null;
    relative_return_10d_percent?: number | null;
    relative_return_20d_percent?: number | null;
    relative_return_60d_percent?: number | null;
    relative_strength_percentile?: number | null;
    today_main_force_net_yi?: number | null;
    cumulative_5d_net_yi?: number | null;
    cumulative_20d_net_yi?: number | null;
    advancing_ratio_percent?: number | null;
    distance_from_ma20_percent?: number | null;
    distance_from_ma60_percent?: number | null;
    distance_from_20d_high_percent?: number | null;
    volume_ratio_5d_vs_20d?: number | null;
    max_drawdown_20d_percent?: number | null;
  };
  source_dates?: {
    sector_kline_end_date?: string | null;
    sector_price_source?: string | null;
    proxy_member_count?: number | null;
    flow_date?: string | null;
  };
  evidence?: string[];
  risks?: string[];
};

export type MainlineSnapshot = {
  schema_version?: string;
  policy_version?: string;
  decision_at?: string;
  captured_at?: string;
  effective_trade_date?: string | null;
  session_kind?: string | null;
  decision_policy?: string;
  execution_gate_changed?: boolean;
  snapshot_hash?: string;
  sector_count?: number;
  available_count?: number;
  ranking?: string[];
  sectors?: MainlineRegime[];
};

export type SectorOpportunity = {
  sector_label: string;
  track?: string | null;
  score?: number | null;
  research_score?: number | null;
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
  mainline_regime?: MainlineRegime | null;
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
  action_category?: "buy" | "watch_only" | "conditional_wait" | "unknown" | string;
  eligible?: boolean;
  mature?: boolean;
  skipped?: boolean;
  status?: "hit" | "miss" | "pending" | "skipped" | string;
  skip_reason?: string | null;
  horizon_trading_days?: number;
  observed_forward_trading_days?: number;
  baseline_nav_date?: string | null;
  target_nav_date?: string | null;
  partial_change_percent?: number | null;
  direction_aligned?: boolean | null;
  assessment?: string;
  hit_take_profit_within_days?: boolean | null;
  metric_contract_version?: string;
  metrics?: OutcomeMetricResults;
  fee_policy?: FrozenFeePolicy;
  benchmark?: OutcomeBenchmark;
  gross_direction_return_percent?: number | null;
  gross_direction_hit?: boolean | null;
  positive_net_return_percent?: number | null;
  positive_net_return_hit?: boolean | null;
  gross_excess_return_percent?: number | null;
  gross_excess_hit?: boolean | null;
  net_excess_return_percent?: number | null;
  net_excess_hit?: boolean | null;
  path_metrics?: OutcomePathMetrics;
  no_action_counterfactual?: NoActionCounterfactual;
  selection_baseline_results?: Record<string, unknown>;
};

export type OutcomeLegacyReference = {
  excluded_from_formal_v2: boolean;
  reason?: string;
  report_count?: number;
  total_count?: number;
  recommendation_count?: number;
  eligible_count?: number;
  observation_count?: number;
  mature_count?: number;
  pending_count?: number;
  skipped_count?: number;
  coverage_percent?: number | null;
  hit_count?: number;
  hit_rate_percent?: number | null;
  metrics?: OutcomeMetricSummary;
  by_horizon?: Record<string, OutcomeHorizonStats>;
  by_style?: Record<string, RecommendationAccuracyBucket>;
  summary_lines?: string[];
};

export type DiscoveryOutcomesPayload = {
  schema_version?: string;
  has_data: boolean;
  days?: number;
  horizon?: string;
  supported_horizons?: number[];
  total_count?: number;
  eligible_count?: number;
  mature_count?: number;
  pending_count?: number;
  skipped_count?: number;
  coverage_percent?: number | null;
  hit_count?: number;
  hit_rate_percent?: number | null;
  hit_definition?: string;
  benchmark?: { available: boolean; reason?: string; [key: string]: unknown };
  metric_contract_version?: string;
  metrics?: OutcomeMetricSummary;
  gross_direction?: OutcomeMetricStats;
  positive_net_return?: OutcomeMetricStats;
  gross_excess?: OutcomeMetricStats;
  net_excess?: OutcomeMetricStats;
  formal_v2_report_count?: number;
  legacy_reference?: OutcomeLegacyReference;
  event_contract?: {
    persistence?: string;
    decision_event_schema_version?: string;
    outcome_observation_schema_version?: string;
    metric_contract_version?: string;
  };
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
  allocation_plan?: DiscoveryAllocationPlan;
  discovery_facts?: {
    sector_opportunities?: DiscoverySectorOpportunity[];
    mainline_snapshot?: MainlineSnapshot;
    market_breadth?: MarketBreadthSignal | null;
    selection_strategy?: SelectionStrategy | string;
    fund_type_preference?: FundTypePreference | string;
    portfolio_gap?: {
      scan_mode?: DiscoveryScanMode | string;
      [key: string]: unknown;
    };
    effective_configuration?: {
      scan_goal?: DiscoveryScanMode | string;
      discovery_strategy?: DiscoveryStrategy | string;
      discovery_strategy_contract?: {
        id?: DiscoveryStrategy | string;
        label?: string;
        target_horizon?: string;
        signal_windows_trading_days?: number[];
        candidate_drawdown_policy?: string;
        quant_coverage_policy?: string;
      };
      selection_policy?: string;
      share_class_policy?: string;
      legacy_fund_type_preference?: FundTypePreference | string;
    };
    data_evidence_guard?: DiscoveryDataEvidenceGuard;
    candidate_quality_summary?: DiscoveryCandidateQualitySummary;
    risk_context?: DiscoveryRiskContext;
    allocation_plan?: DiscoveryAllocationPlan;
    [key: string]: unknown;
  };
  caveats: string[];
  /** M4/M5：双向 guard 因证据强烈共振剔除的候选（结构化，不必解析 caveats 文案）。 */
  eliminated_candidates?: EliminatedCandidate[];
  provider: string;
  analysis_mode?: AnalysisMode;
  decision_events?: DiscoveryDecisionEvent[];
  decision_contract?: Record<string, unknown>;
};

export type DiscoverySectorHeat = {
  sector_label: string;
  change_1d_percent?: number | null;
  change_5d_percent?: number | null;
  heat_score?: number | null;
  rising_count?: number | null;
  falling_count?: number | null;
  flat_count?: number | null;
  advancing_ratio_percent?: number | null;
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
  rising_count?: number | null;
  falling_count?: number | null;
  flat_count?: number | null;
  advancing_ratio_percent?: number | null;
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
  systemRolePrompt?: string | null,
) {
  return {
    holdings,
    profile,
    ocr_text: ocrText,
    analysis_mode: "deep",
    system_role_prompt: systemRolePrompt?.trim() || null,
  };
}

export async function startAnalyzeJob(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
  systemRolePrompt?: string | null,
): Promise<string> {
  const response = await apiFetch(`${API_BASE}/api/analyze/async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(
      analysisPayload(holdings, profile, ocrText, systemRolePrompt),
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
    focusSectors?: string[];
    budgetYuan?: number | null;
    fundTypePreference?: FundTypePreference;
    selectionStrategy?: SelectionStrategy;
    scanMode?: DiscoveryScanMode;
    discoveryStrategy?: DiscoveryStrategy;
    systemRolePrompt?: string | null;
  },
): Promise<string> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings,
      profile,
      analysis_mode: "deep",
      focus_sectors: options?.focusSectors ?? [],
      budget_yuan: options?.budgetYuan ?? null,
      fund_type_preference: options?.fundTypePreference ?? "any",
      selection_strategy: options?.selectionStrategy ?? "balanced",
      scan_mode: options?.scanMode ?? "full_market",
      discovery_strategy: options?.discoveryStrategy ?? "opportunity_first",
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
  report_count?: number;
  recommendation_count?: number;
  eligible_count?: number;
  observation_count?: number;
  mature_count?: number;
  skipped_count?: number;
  coverage_percent?: number | null;
  hit_count: number;
  miss_count: number;
  hit_rate_percent: number | null;
  by_horizon?: Record<string, OutcomeHorizonStats>;
  metrics?: OutcomeMetricSummary;
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
  metric_status?: "legacy_experimental" | string;
  is_experimental?: boolean;
  auto_tuning_eligible?: boolean;
  warning?: string;
  has_enough_data: boolean;
  message?: string;
  paired_days?: number;
  report_count?: number;
  selected_report_count?: number;
  formal_v2_report_count?: number;
  horizons?: number[];
  recommendation_count?: number;
  eligible_count?: number;
  observation_count?: number;
  mature_count?: number;
  skipped_count?: number;
  coverage_percent?: number | null;
  by_horizon?: Record<string, OutcomeHorizonStats>;
  metric_contract_version?: string;
  metrics?: OutcomeMetricSummary;
  by_style?: Record<string, RecommendationAccuracyBucket>;
  summary_lines?: string[];
  legacy_reference?: OutcomeLegacyReference;
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
  /** 当前温度计口径：盘中准实时或收盘口径。 */
  signal_mode?: "intraday" | "closing";
  /** 更细的数据来源，用于区分盘中、收盘和上一收盘回退。 */
  source_mode?:
    | "intraday_live"
    | "intraday_final"
    | "closing"
    | "previous_close_fallback";
  as_of_datetime?: string | null;
  freshness_seconds?: number | null;
  freshness_status?: "live" | "fresh" | "stale";
  decision_eligible?: boolean;
  decision_status?: string | null;
  decision_message?: string | null;
  advance_count?: number | null;
  decline_count?: number | null;
  flat_count?: number | null;
  suspended_count?: number | null;
  traded_sample_count?: number | null;
  market_sample_count?: number | null;
  source_name?: string | null;
  universe_scope?: string | null;
  activity_percent?: number | null;
  advance_ratio_percent?: number | null;
  decline_ratio_percent?: number | null;
  flat_ratio_percent?: number | null;
  breadth_tone?: string | null;
  real_limit_up_count?: number | null;
  real_limit_down_count?: number | null;
  /** 盘中信号所引用的最近完整收盘锚点。 */
  closing_trade_date?: string | null;
  closing_breadth_percentile?: number | null;
  closing_sentiment_level?: "冰点" | "低迷" | "中性" | "偏热" | "亢奋" | null;
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

export type FundReturnDistributionBinKey =
  | "le_neg5"
  | "neg5_neg3"
  | "neg3_neg1"
  | "neg1_zero"
  | "zero"
  | "zero_one"
  | "one_three"
  | "three_five"
  | "ge_five";

export type FundReturnDistribution = {
  available: boolean;
  stale?: boolean;
  message?: string | null;
  source_mode?: "official_nav";
  source_name?: string | null;
  universe_scope?: string | null;
  as_of_date?: string | null;
  fetched_at?: string | null;
  source_row_count?: number | null;
  valid_count?: number | null;
  missing_count?: number | null;
  coverage_percent?: number | null;
  advance_count?: number | null;
  decline_count?: number | null;
  flat_count?: number | null;
  bins?: Partial<Record<FundReturnDistributionBinKey, number>>;
};

export async function fetchFundReturnDistribution(): Promise<FundReturnDistribution> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/fund-return-distribution`, {
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
  holding_warnings?: HoldingFieldWarning[];
  warning_count?: number;
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
  /** 用户从原平台确认的实际成交份额；缺失时后端只能按金额/净值估算。 */
  confirmed_shares?: number | null;
  /** 原平台实际收取的申购/赎回费；未知必须保持 null，不能当作 0。 */
  fee_yuan?: number | null;
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
  fee_yuan?: number | null;
  shares_source?: "user_confirmed" | "derived_amount_nav" | "unknown" | string;
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
    const errorBody = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(errorBody?.detail || "持仓保存失败");
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

export type LedgerSharesQuality =
  | "user_confirmed"
  | "estimated_baseline"
  | "estimated_legacy"
  | "derived_transaction"
  | "unknown"
  | string;

export type PortfolioLedgerPosition = {
  fund_code: string;
  fund_name?: string | null;
  settled_shares: string | number | null;
  cost_basis_total_cny?: string | number | null;
  average_unit_cost?: string | number | null;
  market_value_cny?: string | number | null;
  shares_quality: LedgerSharesQuality;
  cost_quality?: LedgerSharesQuality;
};

export type PortfolioLedgerBaselineStatus = {
  schema_version?: string;
  status: "confirmed" | "estimated" | "missing" | "partial" | string;
  ledger_version?: string | null;
  position_as_of?: string | null;
  captured_at?: string | null;
  position_complete?: boolean;
  cash?: {
    balance_cny: string | number | null;
    status: "known" | "unknown" | "estimated" | string;
  };
  positions: PortfolioLedgerPosition[];
  message?: string;
};

export type ConfirmPortfolioLedgerBaselineRequest = {
  as_of_date: string;
  cash_balance_yuan?: number | null;
  positions: Array<{
    fund_code: string;
    confirmed_shares: number;
    cost_basis_total_yuan?: number | null;
  }>;
};

export async function fetchPortfolioLedgerBaseline(): Promise<PortfolioLedgerBaselineStatus> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/ledger-baseline`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function confirmPortfolioLedgerBaseline(
  payload: ConfirmPortfolioLedgerBaselineRequest,
): Promise<PortfolioLedgerBaselineStatus> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/ledger-baseline`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
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
  type FundRecommendationPartial,
  type StreamingPartialField,
  type StreamingReportEvents,
  type StreamingReportState,
} from "@/lib/streamApi";
