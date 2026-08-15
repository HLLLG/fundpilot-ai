import type { TradingSession } from "@/lib/api";
import {
  peekClientCacheAgeMs,
  readClientCache,
  writeClientCache,
} from "@/lib/clientCache";

export const TRADING_SESSION_CACHE_KEY = "trading-session";
export const TRADING_SESSION_STALE_MS = 5 * 60 * 1000;
/** 刷新页面后仍可读的上次行情日（仅作失败兜底展示） */
const TRADING_SESSION_SESSION_MAX_AGE_MS = 60 * 60 * 1000;

export function readTradingSessionCache(): TradingSession | null {
  return (
    readClientCache<TradingSession>(TRADING_SESSION_CACHE_KEY, -1, "memory") ??
    readClientCache<TradingSession>(
      TRADING_SESSION_CACHE_KEY,
      TRADING_SESSION_SESSION_MAX_AGE_MS,
      "session",
    )
  );
}

export function isTradingSessionCacheFresh(): boolean {
  const ageMs = peekClientCacheAgeMs(TRADING_SESSION_CACHE_KEY, "memory");
  return ageMs != null && ageMs <= TRADING_SESSION_STALE_MS;
}

export function writeTradingSessionCache(session: TradingSession): void {
  writeClientCache(TRADING_SESSION_CACHE_KEY, session, "memory");
  writeClientCache(TRADING_SESSION_CACHE_KEY, session, "session");
}
