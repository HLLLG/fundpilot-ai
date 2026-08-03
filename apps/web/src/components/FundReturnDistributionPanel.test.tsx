// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FundReturnDistributionPanel,
  shouldRefreshCurrentTradeDayDistribution,
} from "@/components/FundReturnDistributionPanel";
import { deleteClientCache } from "@/lib/clientCache";
import { saveFundReturnDistributionCache } from "@/lib/storage";

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

  it("replaces a previous-day local bootstrap when current-day data is unavailable", async () => {
    saveFundReturnDistributionCache({
      available: true,
      source_mode: "official_nav",
      as_of_date: "2026-07-31",
      valid_count: 9,
      advance_count: 0,
      decline_count: 0,
      flat_count: 9,
      bins: { zero: 9 },
    });
    apiMocks.fetchTradingSession.mockResolvedValue({
      is_trading_day: true,
      session_kind: "trading_day_after_close",
      calendar_date: "2026-08-03",
      effective_trade_date: "2026-08-03",
    });
    apiMocks.fetchFundReturnDistribution.mockResolvedValue({
      available: false,
      stale: true,
      source_mode: "intraday_estimate",
      as_of_date: "2026-08-03",
      message: "当日基金涨跌分布尚未准备好。",
    });

    render(<FundReturnDistributionPanel />);

    expect(await screen.findByText("当日基金涨跌分布尚未准备好。")).toBeTruthy();
    expect(screen.queryByLabelText("基金日增长率九档分布")).toBeNull();
    expect(screen.getByText(/实时估值 · 截至 2026-08-03/)).toBeTruthy();
  });
});


describe("shouldRefreshCurrentTradeDayDistribution gating", () => {
  // 闸门逻辑是纯函数：不挂组件、不用定时器，避免 useCachedFetch 的模块级
  // in-flight Map 在 fake-timer 下跨用例污染。定时器骨架抄自 MarketBreadthGauge，
  // 其 setInterval + visibility 已在那边覆盖，这里只测"何时该发请求"的判定。
  const currentSession = {
    is_trading_day: true,
    calendar_date: "2026-08-03",
    effective_trade_date: "2026-08-03",
  };
  const intraday = {
    available: true,
    source_mode: "intraday_estimate" as const,
    as_of_date: "2026-08-03",
  };

  it("keeps checking the current trade date during continuous trading, lunch and after close", () => {
    for (const session_kind of [
      "trading_day_intraday",
      "trading_day_pre_close",
      "trading_day_after_close",
    ]) {
      expect(
        shouldRefreshCurrentTradeDayDistribution(
          { ...currentSession, session_kind },
          intraday,
        ),
      ).toBe(true);
    }
  });

  it("stops once same-day official NAV is available", () => {
    expect(
      shouldRefreshCurrentTradeDayDistribution(
        { ...currentSession, session_kind: "trading_day_after_close" },
        { available: true, source_mode: "official_nav", as_of_date: "2026-08-03" },
      ),
    ).toBe(false);
  });

  it("skips before open, on non-trading days and without cached data", () => {
    expect(
      shouldRefreshCurrentTradeDayDistribution(
        { ...currentSession, session_kind: "trading_day_pre_open" },
        intraday,
      ),
    ).toBe(false);
    expect(
      shouldRefreshCurrentTradeDayDistribution(
        { ...currentSession, is_trading_day: false, session_kind: "non_trading_day" },
        intraday,
      ),
    ).toBe(false);
    expect(
      shouldRefreshCurrentTradeDayDistribution(
        { ...currentSession, session_kind: "trading_day_intraday" },
        null,
      ),
    ).toBe(false);
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
      message: "盘中为估算参考。",
      valid_count: 9,
      advance_count: 4,
      decline_count: 4,
      flat_count: 1,
      bins: { zero: 9 },
    });

    render(<FundReturnDistributionPanel />);

    expect(await screen.findByText("基金涨跌分布")).toBeTruthy();
    expect(await screen.findByText(/实时估值 · 截至 2026-07-26/)).toBeTruthy();
    expect(screen.getByRole("note").textContent).toContain("盘中为估算参考");
  });
});
