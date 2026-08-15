import { describe, expect, it } from "vitest";
import {
  isTradingSessionCacheFresh,
  TRADING_SESSION_CACHE_KEY,
  writeTradingSessionCache,
} from "@/lib/holdingDetailCache";
import { peekClientCacheAgeMs, readClientCache } from "@/lib/clientCache";
import type { TradingSession } from "@/lib/api";

const SESSION: TradingSession = {
  timezone: "Asia/Shanghai",
  local_datetime: "2026-06-08 10:00",
  calendar_date: "2026-06-08",
  effective_trade_date: "2026-06-08",
  is_trading_day: true,
  is_continuous_trading: true,
  session_kind: "trading_day_intraday",
  market_open_time: "09:30",
  decision_window: "盘中",
  market_close_time: "15:00",
};

describe("holdingDetailCache trading session", () => {
  it("stores trading session for the clock bar, not holdings detail", () => {
    writeTradingSessionCache(SESSION);
    expect(readClientCache(TRADING_SESSION_CACHE_KEY, -1, "memory")).toEqual(SESSION);
    expect(isTradingSessionCacheFresh()).toBe(true);
    const ageMs = peekClientCacheAgeMs(TRADING_SESSION_CACHE_KEY, "memory");
    expect(ageMs).not.toBeNull();
    expect(ageMs!).toBeLessThan(1000);
  });
});
