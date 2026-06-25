import type { HoldingDetail, SectorIntradayResult, TradingSession } from "@/lib/api";
import {
  buildClientCacheKey,
  peekClientCacheAgeMs,
  readClientCache,
  writeClientCache,
} from "@/lib/clientCache";
import type { IntradayQuery } from "@/lib/profileSector";

/** 详情弹窗：先展示缓存，缓存期内也会静默后台刷新（stale-while-revalidate） */
export const HOLDING_DETAIL_STALE_MS = 5 * 60 * 1000;

/** 板块分时：盘中 60s / 收盘后 15min；展示缓存后仍会静默拉取最新 */
export const INTRADAY_STALE_MS_LIVE = 60 * 1000;
export const INTRADAY_STALE_MS_CLOSED = 15 * 60 * 1000;

export const TRADING_SESSION_CACHE_KEY = "trading-session";
export const TRADING_SESSION_STALE_MS = 5 * 60 * 1000;

export function buildHoldingDetailCacheKey(
  userId: number | null | undefined,
  fundCode: string | null | undefined,
): string {
  return buildClientCacheKey("holding-detail", userId ?? "anon", fundCode);
}

export function buildIntradayCacheKey(query: IntradayQuery): string {
  return buildClientCacheKey("sector-intraday", query.source_type, query.source_name);
}

export function readHoldingDetailCache(
  userId: number | null | undefined,
  fundCode: string | null | undefined,
): HoldingDetail | null {
  if (!fundCode) {
    return null;
  }
  return readClientCache<HoldingDetail>(
    buildHoldingDetailCacheKey(userId, fundCode),
    -1,
    "memory",
  );
}

export function isHoldingDetailCacheFresh(
  userId: number | null | undefined,
  fundCode: string | null | undefined,
): boolean {
  if (!fundCode) {
    return false;
  }
  const ageMs = peekClientCacheAgeMs(buildHoldingDetailCacheKey(userId, fundCode), "memory");
  return ageMs != null && ageMs <= HOLDING_DETAIL_STALE_MS;
}

export function writeHoldingDetailCache(
  userId: number | null | undefined,
  fundCode: string,
  detail: HoldingDetail,
): void {
  writeClientCache(buildHoldingDetailCacheKey(userId, fundCode), detail, "memory");
}

export function readIntradayCache(query: IntradayQuery): SectorIntradayResult | null {
  return readClientCache<SectorIntradayResult>(buildIntradayCacheKey(query), -1, "memory");
}

export function isIntradayCacheFresh(
  query: IntradayQuery,
  options: { liveSession: boolean },
): boolean {
  const staleMs = options.liveSession ? INTRADAY_STALE_MS_LIVE : INTRADAY_STALE_MS_CLOSED;
  const ageMs = peekClientCacheAgeMs(buildIntradayCacheKey(query), "memory");
  return ageMs != null && ageMs <= staleMs;
}

export function writeIntradayCache(query: IntradayQuery, result: SectorIntradayResult): void {
  writeClientCache(buildIntradayCacheKey(query), result, "memory");
}

export function readTradingSessionCache(): TradingSession | null {
  return readClientCache<TradingSession>(TRADING_SESSION_CACHE_KEY, -1, "memory");
}

export function isTradingSessionCacheFresh(): boolean {
  const ageMs = peekClientCacheAgeMs(TRADING_SESSION_CACHE_KEY, "memory");
  return ageMs != null && ageMs <= TRADING_SESSION_STALE_MS;
}

export function writeTradingSessionCache(session: TradingSession): void {
  writeClientCache(TRADING_SESSION_CACHE_KEY, session, "memory");
}

export function isLiveTradingSessionKind(sessionKind: string | undefined): boolean {
  return (
    sessionKind === "trading_day_intraday" ||
    sessionKind === "trading_day_pre_close"
  );
}
