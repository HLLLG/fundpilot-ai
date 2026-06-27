import { fetchTradingSession, type TradingSession } from "@/lib/api";
import {
  isTradingSessionCacheFresh,
  readTradingSessionCache,
  writeTradingSessionCache,
} from "@/lib/holdingDetailCache";

const RETRY_DELAYS_MS = [0, 1000, 2500] as const;

let inFlightRefresh: Promise<TradingSession> | null = null;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export async function fetchTradingSessionWithRetry(): Promise<TradingSession> {
  let lastError: unknown;
  for (const delay of RETRY_DELAYS_MS) {
    if (delay > 0) {
      await sleep(delay);
    }
    try {
      return await fetchTradingSession();
    } catch (error) {
      lastError = error;
    }
  }
  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error("Failed to fetch trading session");
}

function refreshTradingSessionDeduped(): Promise<TradingSession> {
  if (!inFlightRefresh) {
    inFlightRefresh = fetchTradingSessionWithRetry().finally(() => {
      inFlightRefresh = null;
    });
  }
  return inFlightRefresh;
}

export type TradingSessionLoadMeta = {
  fromCache: boolean;
};

/**
 * Stale-while-revalidate：有缓存则先展示，后台带重试刷新；全失败时保留缓存。
 */
export function hydrateTradingSession(
  onSession: (session: TradingSession, meta: TradingSessionLoadMeta) => void,
  onError?: () => void,
): () => void {
  let cancelled = false;
  const cached = readTradingSessionCache();
  if (cached) {
    onSession(cached, { fromCache: true });
  }

  if (cached && isTradingSessionCacheFresh()) {
    return () => {
      cancelled = true;
    };
  }

  void (async () => {
    try {
      const session = await refreshTradingSessionDeduped();
      if (cancelled) {
        return;
      }
      writeTradingSessionCache(session);
      onSession(session, { fromCache: false });
    } catch {
      if (cancelled) {
        return;
      }
      if (!cached) {
        onError?.();
      }
    }
  })();

  return () => {
    cancelled = true;
  };
}
