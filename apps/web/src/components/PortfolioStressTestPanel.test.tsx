// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";

import { PortfolioStressTestPanel } from "@/components/PortfolioStressTestPanel";
import {
  fetchPortfolioFeeEvidence,
  fetchPortfolioStressTest,
} from "@/lib/api";


vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchPortfolioStressTest: vi.fn(),
    fetchPortfolioFeeEvidence: vi.fn(),
  };
});


afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});


it("loads stress and realized-fee evidence only after expansion", async () => {
  vi.mocked(fetchPortfolioStressTest).mockResolvedValue({
    schema_version: "portfolio_stress_test.v1",
    model_version: "current_weight_historical_replay.v1",
    mode: "shadow_diagnostic_only",
    generated_at: "2026-07-18T08:00:00+00:00",
    status: "available",
    available: true,
    automatic_action_allowed: false,
    forecast: false,
    interpretation: "historical_replay_not_loss_forecast",
    lookback_days: 252,
    sample: {
      common_return_days: 100,
      start_date: "2026-01-01",
      end_date: "2026-06-01",
      holding_count: 2,
      total_current_holding_amount_yuan: 10_000,
    },
    scenarios: [
      {
        scenario_id: "worst_observed_1d",
        label: "历史最差单日",
        method: "worst_observed_rolling_compound_return",
        window_trading_days: 1,
        return_percent: -12.5,
        estimated_loss_yuan: 1_250,
        start_date: "2026-03-02",
        end_date: "2026-03-02",
        forecast: false,
      },
    ],
    reason_codes: [],
    notices: [],
    validation: { status: "valid", error_codes: [] },
  });
  vi.mocked(fetchPortfolioFeeEvidence).mockResolvedValue({
    schema_version: "portfolio_realized_fee_evidence.v1",
    status: "collecting",
    evidence_basis: "user_recorded_actual_transaction_fee",
    external_receipt_verified: false,
    confirmed_transaction_count: 4,
    known_fee_transaction_count: 3,
    unknown_fee_transaction_count: 1,
    known_fee_coverage_percent: 75,
    known_fee_transaction_amount_yuan: 8_000,
    total_recorded_fee_yuan: 12,
    weighted_recorded_fee_percent: 0.15,
    candidate_cost_model_eligible: false,
    automatic_model_update_allowed: false,
    notices: [],
  });

  const { rerender } = render(<PortfolioStressTestPanel enabled={false} />);
  expect(fetchPortfolioStressTest).not.toHaveBeenCalled();
  expect(fetchPortfolioFeeEvidence).not.toHaveBeenCalled();

  rerender(<PortfolioStressTestPanel enabled />);

  expect(await screen.findByText("当前权重历史压力重放")).toBeInTheDocument();
  expect(screen.getByText("-12.50%")).toBeInTheDocument();
  expect(screen.getByText("覆盖 75.0%")).toBeInTheDocument();
  expect(screen.getByText(/1 笔仍未知/)).toBeInTheDocument();
  expect(screen.getByText(/相对已知交易金额 0.150%/)).toBeInTheDocument();
  expect(screen.getByText(/不是未来预测/)).toBeInTheDocument();
});


it("shows missing evidence as unavailable instead of zero risk", async () => {
  vi.mocked(fetchPortfolioStressTest).mockResolvedValue({
    schema_version: "portfolio_stress_test.v1",
    model_version: "current_weight_historical_replay.v1",
    mode: "shadow_diagnostic_only",
    generated_at: "2026-07-18T08:00:00+00:00",
    status: "insufficient_evidence",
    available: false,
    automatic_action_allowed: false,
    forecast: false,
    interpretation: "historical_replay_not_loss_forecast",
    lookback_days: 252,
    sample: {
      common_return_days: 20,
      holding_count: 2,
      total_current_holding_amount_yuan: 10_000,
    },
    scenarios: [],
    reason_codes: ["common_return_sample_insufficient"],
    notices: [],
    validation: { status: "valid", error_codes: [] },
  });
  vi.mocked(fetchPortfolioFeeEvidence).mockResolvedValue({
    schema_version: "portfolio_realized_fee_evidence.v1",
    status: "not_started",
    evidence_basis: "user_recorded_actual_transaction_fee",
    external_receipt_verified: false,
    confirmed_transaction_count: 0,
    known_fee_transaction_count: 0,
    unknown_fee_transaction_count: 0,
    known_fee_coverage_percent: null,
    known_fee_transaction_amount_yuan: 0,
    total_recorded_fee_yuan: null,
    weighted_recorded_fee_percent: null,
    candidate_cost_model_eligible: false,
    automatic_model_update_allowed: false,
    notices: [],
  });

  render(<PortfolioStressTestPanel enabled />);

  expect(await screen.findByText(/共同覆盖的交易日不足 60 天/)).toBeInTheDocument();
  expect(screen.getByText(/空值不代表零风险/)).toBeInTheDocument();
  expect(screen.queryByText("0.00%")).not.toBeInTheDocument();
});
