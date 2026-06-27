import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TradingSession } from "@/lib/api";
import { deleteClientCache } from "@/lib/clientCache";
import {
  TRADING_SESSION_CACHE_KEY,
  TRADING_SESSION_STALE_MS,
  writeTradingSessionCache,
} from "@/lib/holdingDetailCache";
import {
  fetchTradingSessionWithRetry,
  hydrateTradingSession,
} from "@/lib/tradingSessionClient";

const sampleSession: TradingSession = {
  timezone: "Asia/Shanghai",
  local_datetime: "2026-06-26 13:40",
  calendar_date: "2026-06-26",
  effective_trade_date: "2026-06-26",
  is_trading_day: true,
  session_kind: "trading_day_intraday",
  minutes_to_close: 20,
  decision_window: "盘中",
  market_close_time: "15:00",
  market_open_time: "09:30",
};

vi.mock("@/lib/api", () => ({
  fetchTradingSession: vi.fn(),
}));

import { fetchTradingSession } from "@/lib/api";

describe("tradingSessionClient", () => {
  beforeEach(() => {
    vi.mocked(fetchTradingSession).mockReset();
    deleteClientCache(TRADING_SESSION_CACHE_KEY, "memory");
    deleteClientCache(TRADING_SESSION_CACHE_KEY, "session");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries fetch before succeeding", async () => {
    vi.mocked(fetchTradingSession)
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValueOnce(sampleSession);

    const promise = fetchTradingSessionWithRetry();
    await vi.runAllTimersAsync();
    await expect(promise).resolves.toEqual(sampleSession);
    expect(fetchTradingSession).toHaveBeenCalledTimes(2);
  });

  it("hydrate shows cache first then network update", async () => {
    writeTradingSessionCache({
      ...sampleSession,
      effective_trade_date: "2026-06-25",
    });
    vi.advanceTimersByTime(TRADING_SESSION_STALE_MS + 1);
    vi.mocked(fetchTradingSession).mockResolvedValue(sampleSession);

    const seen: string[] = [];
    const cancel = hydrateTradingSession((session, meta) => {
      seen.push(`${session.effective_trade_date}:${meta.fromCache}`);
    });

    await vi.runAllTimersAsync();
    cancel();

    expect(seen).toEqual(["2026-06-25:true", "2026-06-26:false"]);
  });

  it("hydrate keeps cache when network fails after retries", async () => {
    writeTradingSessionCache(sampleSession);
    vi.advanceTimersByTime(TRADING_SESSION_STALE_MS + 1);
    vi.mocked(fetchTradingSession).mockRejectedValue(new Error("Failed to fetch"));

    const onError = vi.fn();
    const seen: string[] = [];
    const cancel = hydrateTradingSession(
      (session) => {
        seen.push(session.effective_trade_date);
      },
      onError,
    );

    await vi.runAllTimersAsync();
    cancel();

    expect(seen).toEqual(["2026-06-26"]);
    expect(onError).not.toHaveBeenCalled();
    expect(fetchTradingSession).toHaveBeenCalledTimes(3);
  });

  it("hydrate calls onError when no cache and network fails", async () => {
    vi.mocked(fetchTradingSession).mockRejectedValue(new Error("Failed to fetch"));
    const onError = vi.fn();

    const cancel = hydrateTradingSession(() => undefined, onError);
    await vi.runAllTimersAsync();
    cancel();

    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("hydrate dedupes concurrent in-flight refresh", async () => {
    let resolveFetch: ((value: TradingSession) => void) | undefined;
    vi.mocked(fetchTradingSession).mockImplementation(
      () =>
        new Promise<TradingSession>((resolve) => {
          resolveFetch = resolve;
        }),
    );

    const seen: string[] = [];
    const cancelA = hydrateTradingSession((session) => {
      seen.push(`a:${session.effective_trade_date}`);
    });
    const cancelB = hydrateTradingSession((session) => {
      seen.push(`b:${session.effective_trade_date}`);
    });

    resolveFetch?.(sampleSession);
    await vi.runAllTimersAsync();
    cancelA();
    cancelB();

    expect(fetchTradingSession).toHaveBeenCalledTimes(1);
    expect(seen).toEqual(["a:2026-06-26", "b:2026-06-26"]);
  });

  it("hydrate skips network when cache is still fresh", async () => {
    writeTradingSessionCache(sampleSession);

    const cancel = hydrateTradingSession(() => undefined);
    await vi.runAllTimersAsync();
    cancel();

    expect(fetchTradingSession).not.toHaveBeenCalled();
  });
});
