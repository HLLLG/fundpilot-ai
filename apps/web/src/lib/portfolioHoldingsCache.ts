import type { Holding, PortfolioSummary } from "@/lib/api";

import { stripHoldingsQuoteFields } from "@/lib/holdingMetrics";



const LEGACY_CACHE_KEY = "fundpilot-portfolio-holdings-v1";
const LEGACY_USER_CACHE_KEY_PREFIX = "fundpilot-portfolio-holdings-v2";
const CACHE_KEY_PREFIX = "fundpilot-portfolio-holdings-v3";



export type CachedPortfolioHoldings = {

  holdings: Holding[];

  portfolio_summary?: PortfolioSummary | null;

  refreshed_at?: string | null;

  cached_at?: string;

};



type CacheEnvelope = CachedPortfolioHoldings & {

  fetchedAt: number;

};

type CacheUserId = number | null | undefined;

function cacheKeyForUser(userId: CacheUserId): string | null {
  if (userId == null) {
    return null;
  }
  return `${CACHE_KEY_PREFIX}:${userId}`;
}

function legacyCacheKeysForUser(userId: CacheUserId): string[] {
  const keys = [LEGACY_CACHE_KEY];
  if (userId != null) {
    keys.push(`${LEGACY_USER_CACHE_KEY_PREFIX}:${userId}`);
  }
  return keys;
}



/** localStorage 仅缓存金额/名称等静态字段，板块涨跌由后端缓存提供。 */

function holdingsForCache(holdings: Holding[]): Holding[] {

  return stripHoldingsQuoteFields(holdings);

}



export function loadCachedPortfolioHoldings(userId: CacheUserId): CachedPortfolioHoldings | null {

  if (typeof window === "undefined") {

    return null;

  }
  const cacheKey = cacheKeyForUser(userId);
  if (!cacheKey) {
    return null;
  }

  try {

    const raw = window.localStorage.getItem(cacheKey);

    if (!raw) {

      return null;

    }

    const envelope = JSON.parse(raw) as CacheEnvelope;

    if (!Array.isArray(envelope.holdings)) {

      return null;

    }

    const refreshedAt = envelope.refreshed_at ?? null;

    return {

      holdings: holdingsForCache(envelope.holdings),

      portfolio_summary: envelope.portfolio_summary ?? null,

      refreshed_at: refreshedAt,

      cached_at: new Date(envelope.fetchedAt).toISOString(),

    };

  } catch {

    return null;

  }

}



export function saveCachedPortfolioHoldings(
  userId: CacheUserId,
  payload: CachedPortfolioHoldings,
): void {

  if (typeof window === "undefined") {

    return;

  }
  const cacheKey = cacheKeyForUser(userId);
  if (!cacheKey) {
    return;
  }

  const refreshedAt = payload.refreshed_at ?? null;

  const envelope: CacheEnvelope = {

    holdings: holdingsForCache(payload.holdings),

    portfolio_summary: payload.portfolio_summary ?? null,

    refreshed_at: refreshedAt,

    cached_at: new Date().toISOString(),

    fetchedAt: Date.now(),

  };

  try {

    window.localStorage.setItem(cacheKey, JSON.stringify(envelope));
    for (const legacyKey of legacyCacheKeysForUser(userId)) {
      window.localStorage.removeItem(legacyKey);
    }

  } catch {

    // localStorage 满或隐私模式时静默降级

  }

}



export function clearCachedPortfolioHoldings(userId?: CacheUserId): void {

  if (typeof window === "undefined") {

    return;

  }

  try {

    const cacheKey = cacheKeyForUser(userId);
    if (cacheKey) {
      window.localStorage.removeItem(cacheKey);
    }
    for (const legacyKey of legacyCacheKeysForUser(userId)) {
      window.localStorage.removeItem(legacyKey);
    }

  } catch {

    // ignore

  }

}

