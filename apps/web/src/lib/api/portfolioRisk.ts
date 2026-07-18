import { API_BASE, apiFetch } from "@/lib/api/core";


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


export type PortfolioStressScenario = {
  scenario_id: string;
  label: string;
  method: string;
  window_trading_days: number;
  return_percent: number;
  estimated_loss_yuan: number;
  start_date: string;
  end_date: string;
  tail_observation_count?: number;
  forecast: false;
};


export type PortfolioStressTest = {
  schema_version: "portfolio_stress_test.v1" | string;
  model_version: string;
  mode: string;
  generated_at: string;
  status: "available" | "insufficient_evidence" | string;
  available: boolean;
  automatic_action_allowed: false;
  forecast: false;
  interpretation: string;
  lookback_days: number;
  sample: {
    common_return_days: number;
    start_date?: string | null;
    end_date?: string | null;
    holding_count: number;
    total_current_holding_amount_yuan: number;
  };
  scenarios: PortfolioStressScenario[];
  reason_codes: string[];
  missing_fund_codes?: string[];
  notices: string[];
  validation: { status: "valid" | "invalid" | string; error_codes: string[] };
};


export type PortfolioFeeEvidence = {
  schema_version: "portfolio_realized_fee_evidence.v1" | string;
  status: "not_started" | "collecting" | "available" | string;
  evidence_basis: string;
  external_receipt_verified: false;
  confirmed_transaction_count: number;
  known_fee_transaction_count: number;
  unknown_fee_transaction_count: number;
  known_fee_coverage_percent: number | null;
  known_fee_transaction_amount_yuan: number;
  total_recorded_fee_yuan: number | null;
  weighted_recorded_fee_percent: number | null;
  candidate_cost_model_eligible: false;
  automatic_model_update_allowed: false;
  notices: string[];
};


async function readJson<T>(path: string): Promise<T> {
  const response = await apiFetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}


export function fetchPortfolioRiskMetrics(): Promise<PortfolioRiskMetrics> {
  return readJson("/api/portfolio/risk-metrics");
}


export function fetchPortfolioRiskCorrelation(
  lookbackDays?: number,
): Promise<PortfolioRiskCorrelation> {
  const query = lookbackDays ? `?lookback_days=${lookbackDays}` : "";
  return readJson(`/api/portfolio/risk-correlation${query}`);
}


export function fetchPortfolioStressTest(lookbackDays = 252): Promise<PortfolioStressTest> {
  return readJson(`/api/portfolio/stress-test?lookback_days=${lookbackDays}`);
}


export function fetchPortfolioFeeEvidence(): Promise<PortfolioFeeEvidence> {
  return readJson("/api/portfolio/fee-evidence");
}
