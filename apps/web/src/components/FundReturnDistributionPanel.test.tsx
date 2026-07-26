// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FundReturnDistributionPanel,
  shouldRefreshIntradayDistribution,
} from "@/components/FundReturnDistributionPanel";
import { deleteClientCache } from "@/lib/clientCache";

const apiMocks = vi.hoisted(() => ({
  fetchFundReturnDistribution: vi.fn(),
  fetchTradingSession: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchFundReturnDistribution: apiMocks.fetchFundReturnDistribution,
    fetchTradingSession: apiMocks.fetchTradingSession,
  };
});

describe("FundReturnDistributionPanel", () => {
  afterEach(() => {
    cleanup();
    deleteClientCache("diagnostics:fund-return-distribution", "memory");
    deleteClientCache("diagnostics:fund-return-distribution", "session");
    window.sessionStorage.clear();
    window.localStorage.clear();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  beforeEach(() => {
    apiMocks.fetchTradingSession.mockReset();
    apiMocks.fetchFundReturnDistribution.mockReset();
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


describe("shouldRefreshIntradayDistribution gating (空跑保护)", () => {
  // 闸门逻辑是纯函数：不挂组件、不用定时器，避免 useCachedFetch 的模块级
  // in-flight Map 在 fake-timer 下跨用例污染。定时器骨架抄自 MarketBreadthGauge，
  // 其 setInterval + visibility 已在那边覆盖，这里只测"何时该发请求"的判定。
  it("refreshes only during continuous trading", () => {
    expect(shouldRefreshIntradayDistribution({ is_continuous_trading: true })).toBe(true);
  });

  it("skips on non-trading days, pre-open, lunch break and after close", () => {
    expect(shouldRefreshIntradayDistribution({ is_continuous_trading: false })).toBe(false);
  });

  it("skips when the session is missing or unreadable", () => {
    expect(shouldRefreshIntradayDistribution(null)).toBe(false);
    expect(shouldRefreshIntradayDistribution(undefined)).toBe(false);
    expect(shouldRefreshIntradayDistribution({})).toBe(false);
  });
});

describe("FundReturnDistributionPanel source subtitle", () => {
  beforeEach(() => {
    apiMocks.fetchTradingSession.mockResolvedValue({ is_continuous_trading: true });
  });

  it("labels the intraday source subtitle distinctly from official NAV", async () => {
    apiMocks.fetchFundReturnDistribution.mockResolvedValue({
      available: true,
      source_mode: "intraday_estimate",
      as_of_datetime: "2026-07-26",
      valid_count: 9,
      advance_count: 4,
      decline_count: 4,
      flat_count: 1,
      bins: { zero: 9 },
    });

    render(<FundReturnDistributionPanel />);

    expect(await screen.findByText("基金涨跌分布")).toBeTruthy();
    expect(await screen.findByText(/实时估值 · 截至 2026-07-26/)).toBeTruthy();
  });
});
