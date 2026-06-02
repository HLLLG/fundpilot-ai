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
  user_note?: string | null;
};

export type InvestorProfile = {
  style: string;
  horizon: string;
  max_drawdown_percent: number;
  concentration_limit_percent: number;
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
  items: Array<{
    fund_code: string;
    fund_name: string;
    previous_action?: string;
    current_action?: string;
    holding_return_before?: number | null;
    holding_return_after?: number | null;
    holding_return_delta?: number | null;
    assessment: string;
  }>;
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
  sector_name?: string | null;
  sector_return_percent?: number | null;
  source: string;
  is_provisional?: boolean;
  raw_text?: string;
  upload_path?: string | null;
};

export type ProfileSyncResult = {
  updated: number;
  created: number;
};

export type PortfolioSummary = {
  total_assets?: number | null;
  daily_profit?: number | null;
  daily_return_percent?: number | null;
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

export type OcrResponse = {
  raw_text: string;
  upload_path: string | null;
  holdings: Holding[];
  error?: string;
  cache_hit?: boolean;
  profile_sync?: ProfileSyncResult;
  portfolio_summary?: PortfolioSummary | null;
  holding_warnings?: HoldingFieldWarning[];
  holding_diffs?: HoldingListDiff[];
  previous_holdings?: Holding[];
  warning_count?: number;
};

export type AnalysisJob = {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  error?: string | null;
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

export async function parseOcr(formData: FormData): Promise<OcrResponse> {
  const response = await fetch(`${API_BASE}/api/ocr`, {
    method: "POST",
    body: formData,
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

export async function waitForAnalysisJob(
  jobId: string,
  options?: { intervalMs?: number; timeoutMs?: number },
): Promise<Report> {
  const intervalMs = options?.intervalMs ?? 1500;
  const timeoutMs = options?.timeoutMs ?? 600_000;
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const job = await fetchAnalysisJob(jobId);
    if (job.status === "completed" && job.report) {
      return job.report;
    }
    if (job.status === "failed") {
      throw new Error(job.error ?? "分析任务失败");
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("分析任务超时，请稍后在历史记录中查看。");
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

export async function exportFundProfiles(): Promise<{ profiles: FundProfile[] }> {
  const response = await fetch(`${API_BASE}/api/fund-profiles/export`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
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

export async function parseFundProfile(formData: FormData): Promise<FundProfile> {
  const response = await fetch(`${API_BASE}/api/fund-profiles/ocr`, {
    method: "POST",
    body: formData,
  });
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
