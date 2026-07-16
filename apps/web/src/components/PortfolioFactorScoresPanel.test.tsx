// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";

import { PortfolioFactorScoresPanel } from "@/components/PortfolioFactorScoresPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

it("shows the typed peer group and per-fund IC reliability for a research model", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        available: true,
        universe_size: 1500,
        model_version: "factor_ic.v2",
        reliability_scope: "per_fund_peer_group",
        factor_reliability: {},
        funds: [
          {
            fund_code: "000001",
            fund_name: "测试混合基金",
            in_universe: true,
            composite_score: 72,
            composite_grade: "A",
            peer_group: "hh",
            peer_group_label: "混合基金",
            peer_count: 677,
            factor_reliability: {
              momentum: { level: "中", basis: "混合同类样本外稳定" },
            },
            factors: {
              momentum: { raw: 1, z: 0.5, percentile: 72 },
              risk_adjusted: { raw: 1, z: 0.4, percentile: 68 },
              drawdown: { raw: -8, z: 0.3, percentile: 64 },
              size: { raw: null, z: null, percentile: null },
            },
          },
        ],
      }),
      text: async () => "",
    }),
  );

  render(<PortfolioFactorScoresPanel enabled />);

  expect(await screen.findByText(/混合基金同类 677 只/)).toBeInTheDocument();
  expect(screen.getByText("IC·中")).toHaveAttribute("title", "混合同类样本外稳定");
  expect(screen.getByText(/分类型研究池 1500 只/)).toBeInTheDocument();
  expect(screen.queryByText(/旧版排行榜可比池/)).not.toBeInTheDocument();
});
