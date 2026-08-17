// @vitest-environment jsdom

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Holding, SectorQuotesStatus } from "@/lib/api";
import {
  fetchSectorQuotesStatus,
  refreshSectorQuotes,
} from "@/lib/api";
import {
  shouldAutoRefreshHoldingsQuotes,
  useSectorQuoteRefresh,
} from "@/lib/useSectorQuoteRefresh";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchSectorQuotesStatus: vi.fn(),
    refreshSectorQuotes: vi.fn(),
    applySectorMapping: vi.fn(),
  };
});

const fetchStatus = vi.mocked(fetchSectorQuotesStatus);
const refreshQuotes = vi.mocked(refreshSectorQuotes);

const HOLDING: Holding = {
  fund_code: "000001",
  fund_name: "测试基金",
  holding_amount: 1000,
  return_percent: 1,
};

function status(overrides: Partial<SectorQuotesStatus> = {}): SectorQuotesStatus {
  return {
    enabled: true,
    ttl_seconds: 60,
    auto_interval_seconds: 180,
    idle_interval_seconds: 10_800,
    auto_refresh_allowed: true,
    session: {
      timezone: "Asia/Shanghai",
      local_datetime: "2026-08-17T10:00:00",
      calendar_date: "2026-08-17",
      effective_trade_date: "2026-08-17",
      is_trading_day: true,
      is_continuous_trading: true,
      session_kind: "trading_day_intraday",
      market_open_time: "09:30",
      market_close_time: "15:00",
      decision_window: "intraday",
    },
    ...overrides,
  };
}

function Probe({ holdings }: { holdings: Holding[] }) {
  useSectorQuoteRefresh({
    holdings,
    onChange: vi.fn(),
  });
  return null;
}

describe("shouldAutoRefreshHoldingsQuotes", () => {
  it("refreshes on an interval only while the trading session allows it", () => {
    expect(
      shouldAutoRefreshHoldingsQuotes({ enabled: true, auto_refresh_allowed: true }, "interval"),
    ).toBe(true);
    expect(
      shouldAutoRefreshHoldingsQuotes({ enabled: true, auto_refresh_allowed: false }, "interval"),
    ).toBe(false);
  });

  it("still catches up when the page becomes visible after hours", () => {
    expect(
      shouldAutoRefreshHoldingsQuotes({ enabled: true, auto_refresh_allowed: false }, "visible"),
    ).toBe(true);
  });

  it("stays idle when sector quotes are disabled", () => {
    expect(
      shouldAutoRefreshHoldingsQuotes({ enabled: false, auto_refresh_allowed: true }, "interval"),
    ).toBe(false);
    expect(
      shouldAutoRefreshHoldingsQuotes({ enabled: false, auto_refresh_allowed: true }, "visible"),
    ).toBe(false);
  });
});

describe("useSectorQuoteRefresh auto polling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    fetchStatus.mockReset();
    refreshQuotes.mockReset();
    fetchStatus.mockResolvedValue(status());
    refreshQuotes.mockResolvedValue({
      ok: true,
      message: "ok",
      holdings: [HOLDING],
      items: [],
      summary: { matched: 1, unresolved: 0, needs_mapping: 0 },
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not poll until holdings exist", async () => {
    render(<Probe holdings={[]} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200_000);
    });
    expect(refreshQuotes).not.toHaveBeenCalled();
  });

  it("silently refreshes on the auto interval while the page stays visible", async () => {
    render(<Probe holdings={[HOLDING]} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchStatus).toHaveBeenCalled();
    expect(refreshQuotes).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(refreshQuotes).toHaveBeenCalledTimes(1);
    expect(refreshQuotes).toHaveBeenCalledWith([HOLDING], {
      forceRefresh: false,
      budget: "fast",
    });
  });

  it("skips interval refreshes after hours, then catches up on restore", async () => {
    fetchStatus.mockResolvedValue(status({ auto_refresh_allowed: false }));
    let visibilityState: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibilityState);

    render(<Probe holdings={[HOLDING]} />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(refreshQuotes).not.toHaveBeenCalled();

    await act(async () => {
      visibilityState = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      visibilityState = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(refreshQuotes).toHaveBeenCalledTimes(1);
  });
});
