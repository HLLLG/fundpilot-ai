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

export type ReportDiff = {
  previous_report_id: string;
  previous_title: string;
  previous_created_at: string;
  risk_level_changed: boolean;
  previous_risk_level: string;
  current_risk_level: string;
  suggested_action_changed: boolean;
  previous_suggested_action: string;
  current_suggested_action: string;
  weighted_return_delta: number;
  holding_changes: Array<{
    type: "added" | "removed" | "changed";
    fund_code?: string;
    fund_name?: string;
    holding_amount?: number;
    return_percent?: number;
    previous_holding_amount?: number;
    previous_return_percent?: number;
    holding_amount_delta?: number;
    return_percent_delta?: number;
  }>;
  recommendation_changes: Array<{
    fund_code: string;
    previous_action?: string | null;
    current_action?: string | null;
  }>;
};

export type ReportDiffResponse = {
  has_previous: boolean;
  diff: ReportDiff | null;
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
  }>;
  summary: string;
  recommendations: string[];
  caveats: string[];
  provider: string;
  analysis_facts?: Record<string, unknown>;
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
};

export type FundTypePreference = "any" | "etf_link" | "no_c_class";

export type SelectionStrategy = "balanced" | "with_new_issue" | "dip_rebound";

export type DiscoveryScanMode = "full_market" | "portfolio_gap";

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
};

export type DiscoveryOutcomeItem = {
  fund_code: string;
  fund_name: string;
  action: string;
  period_change_percent?: number | null;
  direction_aligned?: boolean;
  assessment?: string;
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
  caveats: string[];
  provider: string;
  analysis_mode?: AnalysisMode;
};

export type DiscoverySectorHeat = {
  sector_label: string;
  change_1d_percent?: number | null;
  change_5d_percent?: number | null;
  heat_score?: number | null;
};

export type SectorBoardItem = {
  name: string;
  code?: string | null;
  change_percent?: number | null;
  main_force_net_yi?: number | null;
  rank?: number;
};

export type MarketSectorBoardWidget = {
  trade_date?: string | null;
  session_kind?: string | null;
  available: boolean;
  from_cache?: boolean;
  stale?: boolean;
  message?: string | null;
  top_gainers: SectorBoardItem[];
  top_losers: SectorBoardItem[];
  top_inflow: SectorBoardItem[];
  top_outflow: SectorBoardItem[];
};

export type MarketSectorBoardList = {
  trade_date?: string | null;
  session_kind?: string | null;
  available: boolean;
  from_cache?: boolean;
  stale?: boolean;
  message?: string | null;
  board_type: "industry" | "concept";
  sort: "change" | "inflow";
  items: SectorBoardItem[];
};

export type MarketBoardType = "industry" | "concept";
export type MarketBoardSort = "change" | "inflow";

export type MarketThemeBoardSort = "change" | "streak";

export type MarketThemeBoardItem = {
  sector_label: string;
  change_1d_percent?: number | null;
  consecutive_up_days?: number | null;
  linked_fund_count: number;
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
  message?: string | null;
  sort: MarketThemeBoardSort;
  items: MarketThemeBoardItem[];
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

export async function bindWechatAccount(payload: {
  cloudbaseUid?: string;
  cloudbaseAccessToken?: string;
  cloudbaseTicket?: string;
}): Promise<AuthUser> {
  const response = await apiFetch(`${API_BASE}/api/auth/bind-wechat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail : "绑定失败");
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
  const response = await apiFetch(`${API_BASE}/api/sector-quotes/status`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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

export async function fetchMarketSectorBoardWidget(
  options?: { forceRefresh?: boolean },
): Promise<MarketSectorBoardWidget> {
  const params = new URLSearchParams({ view: "widget" });
  if (options?.forceRefresh) {
    params.set("force_refresh", "true");
  }
  const response = await apiFetch(`${API_BASE}/api/market/sector-boards?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchMarketSectorBoardList(options: {
  boardType: MarketBoardType;
  sort: MarketBoardSort;
  forceRefresh?: boolean;
}): Promise<MarketSectorBoardList> {
  const params = new URLSearchParams({
    view: "list",
    board_type: options.boardType,
    sort: options.sort,
  });
  if (options.forceRefresh) {
    params.set("force_refresh", "true");
  }
  const response = await apiFetch(`${API_BASE}/api/market/sector-boards?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/reports`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function deleteDiscoveryReport(reportId: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/reports/${reportId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
}

export async function fetchDiscoveryReportDiff(reportId: string): Promise<Record<string, unknown>> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/reports/${reportId}/diff`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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

export async function fetchDiscoveryRecommendationAccuracy(
  days = 30,
): Promise<Record<string, unknown>> {
  const response = await apiFetch(
    `${API_BASE}/api/fund-discovery/recommendation-accuracy?days=${days}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchDiscoveryPrompt(): Promise<DiscoveryPromptConfig> {
  const response = await apiFetch(`${API_BASE}/api/discovery-prompt`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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
  const response = await apiFetch(`${API_BASE}/api/reports`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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
  beats_random?: boolean | null;
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

export async function fetchReportDiff(reportId: string): Promise<ReportDiffResponse> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/diff`, {
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
): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/reports/${reportId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, chat_mode: chatMode }),
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
  source: "snapshot" | "profiles" | "empty";
  snapshot_date?: string | null;
  portfolio_summary?: PortfolioSummary | null;
  profile_count?: number;
};

export async function fetchPortfolioHoldings(): Promise<PortfolioHoldingsPayload> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/holdings`, { cache: "no-store" });
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
  const response = await apiFetch(`${API_BASE}/api/investor-profile`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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
  const response = await apiFetch(`${API_BASE}/api/analysis-prompt`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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
