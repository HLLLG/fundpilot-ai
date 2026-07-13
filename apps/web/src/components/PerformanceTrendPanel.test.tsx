// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PerformanceTrendPanel } from "@/components/PerformanceTrendPanel";
import {
  fetchFundNavHistory,
  fetchFundNavHistoryPage,
  fetchIndexDailyHistory,
  getFundTransactions,
  type FundNavHistory,
  type IndexDailyHistory,
} from "@/lib/api";
import { buildClientCacheKey, deleteClientCache } from "@/lib/clientCache";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchFundNavHistory: vi.fn(),
    fetchFundNavHistoryPage: vi.fn(),
    fetchIndexDailyHistory: vi.fn(),
    getFundTransactions: vi.fn(),
  };
});

vi.mock("@/components/PerformanceReturnChart", () => ({
  PerformanceReturnChart: () => <div data-testid="performance-return-chart" />,
}));

const FUND_CODE = "110022";
const OTHER_FUND_CODE = "000001";
const DEFAULT_DAYS = 63;

function fundHistory(): FundNavHistory {
  return {
    fund_code: FUND_CODE,
    fund_name: "测试基金",
    source: "test",
    points: Array.from({ length: 25 }, (_, index) => ({
      date: `2026-06-${String(index + 1).padStart(2, "0")}T00:00:00`,
      nav: 1 + index / 100,
      daily_return_percent: (index + 1) / 10,
    })),
    period_change_percent: 2.4,
  };
}

function indexHistory(): IndexDailyHistory {
  return {
    symbol: "000300",
    name: "沪深300",
    source: "test",
    points: Array.from({ length: 25 }, (_, index) => ({
      date: `2026-06-${String(index + 1).padStart(2, "0")}`,
      close: 4000 + index,
    })),
  };
}

function clearHistoryCaches() {
  deleteClientCache(buildClientCacheKey("fund-nav-history", FUND_CODE, DEFAULT_DAYS));
  deleteClientCache(buildClientCacheKey("fund-nav-history", OTHER_FUND_CODE, DEFAULT_DAYS));
  deleteClientCache(buildClientCacheKey("index-daily", "000300", DEFAULT_DAYS));
}

beforeEach(() => {
  clearHistoryCaches();
  vi.mocked(fetchFundNavHistory).mockResolvedValue(fundHistory());
  vi.mocked(fetchFundNavHistoryPage).mockResolvedValue({
    fund_code: FUND_CODE,
    fund_name: "测试基金",
    source: "test",
    points: [],
    has_more: false,
    next_before: null,
  });
  vi.mocked(fetchIndexDailyHistory).mockResolvedValue(indexHistory());
  vi.mocked(getFundTransactions).mockResolvedValue({ transactions: [] });
});

afterEach(() => {
  cleanup();
  clearHistoryCaches();
  vi.clearAllMocks();
});

describe("PerformanceTrendPanel NAV preview", () => {
  it("derives the latest 22 preview rows from the default history request", async () => {
    render(
      <PerformanceTrendPanel
        fundCode={FUND_CODE}
        fundName="测试基金"
      />,
    );

    expect(await screen.findByTestId("performance-return-chart")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("2026-06-25")).toBeInTheDocument());

    expect(fetchFundNavHistory).toHaveBeenCalledTimes(1);
    expect(fetchFundNavHistory).toHaveBeenCalledWith(FUND_CODE, DEFAULT_DAYS);
    expect(fetchFundNavHistoryPage).not.toHaveBeenCalled();

    const previewDates = screen
      .getAllByText(/^2026-06-/)
      .map((element) => element.textContent);
    expect(previewDates).toEqual(
      Array.from({ length: 22 }, (_, index) => `2026-06-${String(25 - index).padStart(2, "0")}`),
    );
    expect(screen.queryByText("2026-06-03")).not.toBeInTheDocument();

    const latestRow = screen.getByText("2026-06-25").parentElement;
    expect(latestRow).not.toBeNull();
    expect(within(latestRow as HTMLElement).getByText("1.2400")).toBeInTheDocument();
    expect(within(latestRow as HTMLElement).getByText("+2.50%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看历史净值" }));
    await waitFor(() =>
      expect(fetchFundNavHistoryPage).toHaveBeenCalledWith(FUND_CODE, {
        limit: 30,
        before: null,
      }),
    );
  });

  it("keeps the fund preview when the benchmark request fails", async () => {
    vi.mocked(fetchIndexDailyHistory).mockRejectedValueOnce(
      new Error("benchmark unavailable"),
    );

    render(
      <PerformanceTrendPanel
        fundCode={FUND_CODE}
        fundName="测试基金"
      />,
    );

    expect(await screen.findByText("benchmark unavailable")).toBeInTheDocument();
    expect(await screen.findByText("2026-06-25")).toBeInTheDocument();
    expect(fetchFundNavHistoryPage).not.toHaveBeenCalled();
  });

  it("clears the previous fund when a newly selected fund fails to load", async () => {
    const view = render(
      <PerformanceTrendPanel
        fundCode={FUND_CODE}
        fundName="测试基金"
      />,
    );
    expect(await screen.findByText("2026-06-25")).toBeInTheDocument();

    vi.mocked(fetchFundNavHistory).mockRejectedValueOnce(new Error("new fund unavailable"));
    view.rerender(
      <PerformanceTrendPanel
        fundCode={OTHER_FUND_CODE}
        fundName="另一只基金"
      />,
    );

    expect(await screen.findByText("new fund unavailable")).toBeInTheDocument();
    expect(screen.queryByText("2026-06-25")).not.toBeInTheDocument();
  });
});
