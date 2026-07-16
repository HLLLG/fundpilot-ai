// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deleteClientCache } from "@/lib/clientCache";
import { MarketBreadthGauge } from "@/components/MarketBreadthGauge";

const apiMocks = vi.hoisted(() => ({
  fetchMarketBreadth: vi.fn(),
  fetchFundReturnDistribution: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchMarketBreadth: apiMocks.fetchMarketBreadth,
    fetchFundReturnDistribution: apiMocks.fetchFundReturnDistribution,
  };
});

const CACHE_KEY = "diagnostics:market-breadth";

describe("MarketBreadthGauge", () => {
  beforeEach(() => {
    apiMocks.fetchFundReturnDistribution.mockResolvedValue({
      available: false,
      message: "暂无官方净值分布",
    });
  });

  afterEach(() => {
    cleanup();
    deleteClientCache(CACHE_KEY, "memory");
    vi.clearAllMocks();
    vi.useRealTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  it("deduplicates the diagnostics request across concurrent gauges", async () => {
    apiMocks.fetchMarketBreadth.mockResolvedValue({
      available: true,
      trade_date: "2026-07-13",
      sentiment_level: "中性",
      limit_up_count: 42,
    });

    render(
      <>
        <MarketBreadthGauge />
        <MarketBreadthGauge compact />
      </>,
    );

    expect(await screen.findAllByTestId("market-breadth-gauge")).toHaveLength(2);
    expect(apiMocks.fetchMarketBreadth).toHaveBeenCalledTimes(1);
  });

  it("shows the intraday snapshot, freshness and decision participation explicitly", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T01:50:00Z"));
    apiMocks.fetchMarketBreadth.mockResolvedValue({
      available: true,
      trade_date: "2026-07-13",
      signal_mode: "intraday",
      source_mode: "intraday_live",
      as_of_datetime: "2026-07-13T09:45:00+08:00",
      freshness_status: "live",
      decision_eligible: true,
      decision_message: "盘中快照新鲜，可用于降低追涨置信度。",
      sentiment_level: "低迷",
      advance_count: 960,
      decline_count: 4160,
      flat_count: 81,
      activity_percent: 18.45,
      real_limit_up_count: 19,
      real_limit_down_count: 13,
      closing_trade_date: "2026-07-10",
      closing_breadth_percentile: 31.8,
      closing_sentiment_level: "低迷",
    });

    render(<MarketBreadthGauge />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("盘中准实时")).toBeTruthy();
    expect(screen.getByText("更新于 2026-07-13 09:45")).toBeTruthy();
    expect(screen.getByText("数据可参与当前决策")).toBeTruthy();
    expect(screen.getByText("960")).toBeTruthy();
    expect(screen.getByText("4160")).toBeTruthy();
    expect(screen.getByText("18.5%")).toBeTruthy();
    expect(screen.getByText(/收盘锚点：2026-07-10/)).toBeTruthy();
  });

  it("keeps stale data visible but excludes it from the current decision", async () => {
    apiMocks.fetchMarketBreadth.mockResolvedValue({
      available: true,
      trade_date: "2026-07-10",
      signal_mode: "intraday",
      source_mode: "previous_close_fallback",
      as_of_datetime: "2026-07-10T15:00:00+08:00",
      freshness_status: "stale",
      decision_eligible: true,
      sentiment_level: "低迷",
      advance_count: 900,
    });

    render(<MarketBreadthGauge />);

    expect(await screen.findByText("上一交易日收盘回退")).toBeTruthy();
    expect(screen.getByText("数据仅展示，不参与当前决策")).toBeTruthy();
    expect(screen.getByText(/数据已过期，继续展示上次有效快照/)).toBeTruthy();
  });

  it("refreshes every five minutes only while the gauge is visible", async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    apiMocks.fetchMarketBreadth.mockResolvedValue({
      available: true,
      trade_date: "2026-07-13",
      signal_mode: "intraday",
      source_mode: "intraday_live",
      sentiment_level: "中性",
      decision_eligible: true,
    });

    render(<MarketBreadthGauge />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(apiMocks.fetchMarketBreadth).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000);
    });
    expect(apiMocks.fetchMarketBreadth).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });
    expect(apiMocks.fetchMarketBreadth).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(apiMocks.fetchMarketBreadth).toHaveBeenCalledTimes(3);
  });

  it("keeps polling a closing snapshot so a page left open switches to intraday", async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    apiMocks.fetchMarketBreadth
      .mockResolvedValueOnce({
        available: true,
        trade_date: "2026-07-10",
        signal_mode: "closing",
        source_mode: "closing",
        sentiment_level: "低迷",
        decision_eligible: true,
      })
      .mockResolvedValueOnce({
        available: true,
        trade_date: "2026-07-13",
        signal_mode: "intraday",
        source_mode: "intraday_live",
        as_of_datetime: "2026-07-13T09:35:00+08:00",
        sentiment_level: "中性",
        decision_eligible: true,
      });

    render(<MarketBreadthGauge />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("收盘历史口径")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000);
    });

    expect(apiMocks.fetchMarketBreadth).toHaveBeenCalledTimes(2);
    expect(screen.getByText("盘中准实时")).toBeTruthy();
    expect(screen.getByText("更新于 2026-07-13 09:35")).toBeTruthy();
  });

  it("downgrades an intraday snapshot after ten minutes when refresh fails", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T01:50:00Z"));
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    apiMocks.fetchMarketBreadth
      .mockResolvedValueOnce({
        available: true,
        trade_date: "2026-07-13",
        signal_mode: "intraday",
        source_mode: "intraday_live",
        as_of_datetime: "2026-07-13T09:45:00+08:00",
        freshness_status: "live",
        sentiment_level: "中性",
        decision_eligible: true,
        decision_message: "盘中快照新鲜，可参与当前决策。",
      })
      .mockRejectedValueOnce(new Error("network unavailable"));

    render(<MarketBreadthGauge />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("数据可参与当前决策")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000);
    });

    expect(apiMocks.fetchMarketBreadth).toHaveBeenCalledTimes(2);
    expect(screen.getByText("数据仅展示，不参与当前决策")).toBeTruthy();
    expect(screen.getByText(/盘中快照已超过10分钟未更新/)).toBeTruthy();
    expect(screen.getByText(/本次更新失败，正在显示上次数据/)).toBeTruthy();
  });
});
