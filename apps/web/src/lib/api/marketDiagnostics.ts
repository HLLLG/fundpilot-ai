import { API_BASE, apiFetch } from "@/lib/api/core";


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


export type MarketBreadthSignal = {
  available: boolean;
  reason?: string | null;
  message?: string | null;
  stale?: boolean;
  trade_date?: string;
  signal_mode?: "intraday" | "closing";
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


export type ShadowEscalationOutcomeItem = {
  fund_code?: string;
  sector_label?: string | null;
  would_be_action?: string;
  actual_daily_return_percent?: number | null;
  aligned?: boolean;
};


export type ShadowEscalationDigest = {
  available: boolean;
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


export async function fetchMarketBreadth(): Promise<MarketBreadthSignal> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/market-breadth`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}


export async function fetchFundReturnDistribution(): Promise<FundReturnDistribution> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/fund-return-distribution`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}


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
