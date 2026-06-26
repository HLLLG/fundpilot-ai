import type { Holding, PortfolioSummary } from "@/lib/api";

import { stripHoldingsQuoteFields } from "@/lib/holdingMetrics";



const CACHE_KEY = "fundpilot-portfolio-holdings-v1";



export type CachedPortfolioHoldings = {

  holdings: Holding[];

  portfolio_summary?: PortfolioSummary | null;

  refreshed_at?: string | null;

  cached_at?: string;

};



type CacheEnvelope = CachedPortfolioHoldings & {

  fetchedAt: number;

};



/** localStorage 仅缓存金额/名称等静态字段，板块涨跌由后端缓存提供。 */

function holdingsForCache(holdings: Holding[]): Holding[] {

  return stripHoldingsQuoteFields(holdings);

}



export function loadCachedPortfolioHoldings(): CachedPortfolioHoldings | null {

  if (typeof window === "undefined") {

    return null;

  }

  try {

    const raw = window.localStorage.getItem(CACHE_KEY);

    if (!raw) {

      return null;

    }

    const envelope = JSON.parse(raw) as CacheEnvelope;

    if (!Array.isArray(envelope.holdings) || envelope.holdings.length === 0) {

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



export function saveCachedPortfolioHoldings(payload: CachedPortfolioHoldings): void {

  if (typeof window === "undefined" || payload.holdings.length === 0) {

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

    window.localStorage.setItem(CACHE_KEY, JSON.stringify(envelope));

  } catch {

    // localStorage 满或隐私模式时静默降级

  }

}



export function clearCachedPortfolioHoldings(): void {

  if (typeof window === "undefined") {

    return;

  }

  try {

    window.localStorage.removeItem(CACHE_KEY);

  } catch {

    // ignore

  }

}

