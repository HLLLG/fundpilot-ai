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

export type InvestorProfile = {
  style: string;
  horizon: string;
  max_drawdown_percent: number;
  concentration_limit_percent: number;
  expected_investment_amount?: number | null;
  prefer_dca: boolean;
  avoid_chasing: boolean;
};

export type AnalysisMode = "fast" | "deep";

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

export type ReportWeeklyOutcomes = ReportOutcomes & {
  baseline_days?: number;
  baseline_report_id?: string;
  baseline_created_at?: string;
  summary?: string | null;
  hit_count?: number;
  miss_count?: number;
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

export type HoldingListDiff = {
  index?: number | null;
  fund_code: string;
  fund_name: string;
  change_type: "added" | "removed" | "changed" | "unchanged";
  messages: string[];
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

export type PortfolioDashboardData = {
  summary: PortfolioSummary;
  history: PortfolioHistoryPoint[];
  allocation: PortfolioAllocationRow[];
  snapshot_count: number;
  latest_snapshot_date?: string | null;
  profiles?: FundProfile[];
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

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

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

export type AllocatePenetrationResult = {
  holdings: Holding[];
  holding_warnings: HoldingFieldWarning[];
  warning_count: number;
  allocated_total: number;
  account_daily_profit: number;
  method: string;
};

export async function allocatePenetrationDaily(
  holdings: Holding[],
  accountDailyProfit: number,
  accountDailyProfitSource: PortfolioSummary["daily_profit_source"] = "penetration_estimate",
): Promise<AllocatePenetrationResult> {
  const response = await fetch(`${API_BASE}/api/holdings/allocate-penetration-daily`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      holdings,
      account_daily_profit: accountDailyProfit,
      account_daily_profit_source: accountDailyProfitSource,
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function refreshSectorQuotes(
  holdings: Holding[],
  options?: { forceRefresh?: boolean; budget?: "fast" | "accurate" },
): Promise<RefreshSectorQuotesResult> {
  const response = await fetch(`${API_BASE}/api/holdings/refresh-sector-quotes`, {
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
  const response = await fetch(`${API_BASE}/api/sector-mappings/apply`, {
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
  const response = await fetch(`${API_BASE}/api/sector-quotes/status`, { cache: "no-store" });
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
  const response = await fetch(`${API_BASE}/api/holdings/detail`, {
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
  const response = await fetch(`${API_BASE}/api/sector-quotes/intraday?${params}`, {
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
) {
  return {
    holdings,
    profile,
    ocr_text: ocrText,
    analysis_mode: analysisMode,
  };
}

export async function startAnalyzeJob(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
  analysisMode: AnalysisMode = "deep",
): Promise<string> {
  const response = await fetch(`${API_BASE}/api/analyze/async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(analysisPayload(holdings, profile, ocrText, analysisMode)),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.job_id as string;
}

export async function fetchAnalysisJob(jobId: string): Promise<AnalysisJob> {
  const response = await fetch(`${API_BASE}/api/jobs/${jobId}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function listReports(): Promise<Report[]> {
  const response = await fetch(`${API_BASE}/api/reports`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function deleteReport(reportId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
}

export async function fetchReportOutcomes(reportId: string): Promise<ReportOutcomes> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/outcomes`, {
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
  const response = await fetch(
    `${API_BASE}/api/reports/${reportId}/outcomes-weekly?days=${days}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchTradingSession(): Promise<TradingSession> {
  const response = await fetch(`${API_BASE}/api/trading-session`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function exportDatabase(): Promise<void> {
  const response = await fetch(`${API_BASE}/api/database/export`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "fundpilot-app.db";
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function importDatabase(file: File): Promise<{
  ok: boolean;
  imported_from: string;
  target: string;
  backup_path: string;
}> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE}/api/database/import`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchRebalanceSimulation(reportId: string): Promise<RebalanceSimulation> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/rebalance-simulation`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchReportDiff(reportId: string): Promise<ReportDiffResponse> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/diff`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchReportChatHistory(reportId: string): Promise<ReportChatMessage[]> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/chat`, {
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
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/chat`, {
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
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/chat/markdown`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.markdown as string;
}

export async function fetchReportMarkdown(reportId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/markdown`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.markdown as string;
}

export async function importFundProfiles(profiles: FundProfile[]): Promise<{ saved: number }> {
  const response = await fetch(`${API_BASE}/api/fund-profiles/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profiles }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type ParseFundProfileResult = FundProfile & {
  synced_holdings?: Holding[];
  portfolio_summary?: PortfolioSummary | null;
};

export async function parseFundProfile(formData: FormData): Promise<ParseFundProfileResult> {
  const response = await fetch(`${API_BASE}/api/fund-profiles/ocr`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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
  ocr_source?: string;
  fund_code_resolutions?: FundCodeResolution[];
  amount_semantics?: OcrAmountSemantics;
  trading_session?: Record<string, unknown>;
  portfolio_summary?: PortfolioSummary | null;
  holding_warnings?: HoldingFieldWarning[];
  holding_diffs?: HoldingListDiff[];
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
  const response = await fetch(`${API_BASE}/api/ocr`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function applyPortfolioHoldings(holdings: Holding[]): Promise<{
  holdings: Holding[];
  portfolio_summary?: PortfolioSummary | null;
}> {
  const response = await fetch(`${API_BASE}/api/portfolio/apply-holdings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(holdings),
  });
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
  const response = await fetch(`${API_BASE}/api/portfolio/holdings`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioSummary(): Promise<PortfolioSummary> {
  const response = await fetch(`${API_BASE}/api/portfolio/summary`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchInvestorProfile(): Promise<InvestorProfile> {
  const response = await fetch(`${API_BASE}/api/investor-profile`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function saveInvestorProfileRemote(profile: InvestorProfile): Promise<InvestorProfile> {
  const response = await fetch(`${API_BASE}/api/investor-profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchPortfolioDashboard(): Promise<PortfolioDashboardData> {
  const response = await fetch(`${API_BASE}/api/portfolio/dashboard`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function listFundProfiles(): Promise<FundProfile[]> {
  const response = await fetch(`${API_BASE}/api/fund-profiles`, {
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
  const response = await fetch(`${API_BASE}/api/fund-profiles/${encodeURIComponent(fundCode)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ first_purchase_date: firstPurchaseDate }),
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
  const response = await fetch(
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
  const response = await fetch(
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
  const response = await fetch(`${API_BASE}/api/market/index-daily?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
