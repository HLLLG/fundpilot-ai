// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { HoldingProfitPanel } from "@/components/HoldingProfitPanel";
import { fetchFundNavHistory, getFundTransactions } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchFundNavHistory: vi.fn(),
    getFundTransactions: vi.fn(),
  };
});

vi.mock("@/components/FundHoldingTransactions", () => ({
  FundHoldingTransactions: () => <div data-testid="fund-transactions" />,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  vi.mocked(fetchFundNavHistory).mockResolvedValue({
    fund_code: "011373",
    fund_name: "招商前沿医疗保健股票A",
    source: "akshare",
    points: [
      { date: "2026-08-14", nav: 0.841 },
      { date: "2026-08-17", nav: 0.854 },
    ],
  });
  vi.mocked(getFundTransactions).mockResolvedValue({
    transactions: [
      {
        id: "tx-1",
        fund_code: "011373",
        fund_name: "招商前沿医疗保健股票A",
        direction: "buy",
        amount_yuan: 4500.17,
        trade_time: "2026-08-14 14:30:00",
        confirm_date: "2026-08-14",
        status: "confirmed",
        shares_delta: 5346.98,
        nav_on_confirm: 0.8416,
        dedup_key: "k1",
        created_at: "2026-08-14T06:30:00Z",
      },
    ],
  });
});

it("renders a holding-return chart, period tabs, and historical profit rows", async () => {
  render(
    <HoldingProfitPanel
      fundCode="011373"
      fundName="招商前沿医疗保健股票A"
      shares={5346.98}
      unitCost={0.8416}
      currentProfit={66.32}
      currentReturnPercent={1.47}
      yesterdayProfit={54.31}
    />,
  );

  expect(await screen.findByText("持有收益")).toBeInTheDocument();
  expect(screen.getByLabelText(/累计持有收益走势图/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "近3月" })).toHaveAttribute("aria-pressed", "true");
  await waitFor(() => {
    expect(screen.getAllByText("2026-08-17").length).toBeGreaterThan(0);
  });
  expect(screen.getByText("日收益")).toBeInTheDocument();
  expect(screen.getByText("累计收益")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看历史收益" })).toBeEnabled();
});
