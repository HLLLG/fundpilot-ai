// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FundReturnDistributionPanel } from "@/components/FundReturnDistributionPanel";
import { deleteClientCache } from "@/lib/clientCache";

const apiMocks = vi.hoisted(() => ({
  fetchFundReturnDistribution: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, fetchFundReturnDistribution: apiMocks.fetchFundReturnDistribution };
});

describe("FundReturnDistributionPanel", () => {
  afterEach(() => {
    cleanup();
    deleteClientCache("diagnostics:fund-return-distribution", "memory");
    vi.clearAllMocks();
  });

  it("shows the official NAV date, nine buckets and conservation metadata", async () => {
    apiMocks.fetchFundReturnDistribution.mockResolvedValue({
      available: true,
      source_mode: "official_nav",
      as_of_date: "2026-07-16",
      valid_count: 20_325,
      source_row_count: 21_000,
      missing_count: 675,
      coverage_percent: 96.79,
      decline_count: 15_921,
      advance_count: 3_330,
      flat_count: 1_074,
      bins: {
        le_neg5: 335,
        neg5_neg3: 1_893,
        neg3_neg1: 5_754,
        neg1_zero: 7_939,
        zero: 1_074,
        zero_one: 2_530,
        one_three: 739,
        three_five: 61,
        ge_five: 0,
      },
    });

    render(<FundReturnDistributionPanel />);

    expect(await screen.findByText("基金涨跌分布")).toBeTruthy();
    expect(screen.getByText(/截至 2026-07-16/)).toBeTruthy();
    expect(screen.getByText("15,921")).toBeTruthy();
    expect(screen.getByText("3,330")).toBeTruthy();
    expect(screen.getByText(/20,325 个有效基金份额代码/)).toBeTruthy();
    expect(screen.getByText(/675 只缺少当日增长率/)).toBeTruthy();
    expect(screen.getByLabelText("基金日增长率九档分布").children).toHaveLength(9);
  });
});
