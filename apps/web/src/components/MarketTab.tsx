"use client";

import { useEffect } from "react";
import { fetchCnIndexOverview, fetchMarketThemeBoards, fetchUsMarketOverview } from "@/lib/api";
import { buildClientCacheKey, deleteClientCache } from "@/lib/clientCache";
import {
  acceptMarketThemeBoardFresh,
  isMarketThemeBoardUsable,
} from "@/lib/marketThemeBoard";
import {
  acceptCnIndexFresh,
  cnIndexRefreshIntervalMs,
  isCnIndexOverviewUsable,
} from "@/lib/cnIndexOverview";
import { msUntilNextWeekdayWallClock } from "@/lib/marketSessionClock";
import { acceptUsMarketFresh, usRefreshIntervalMs } from "@/lib/usMarketOverview";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { FundReturnDistributionPanel } from "@/components/FundReturnDistributionPanel";
import { MarketIndexStrip } from "@/components/MarketIndexStrip";
import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import { InlineNotice } from "@/components/InlineNotice";

export function MarketFetchNotice({
  error,
  hasData,
  onRetry,
}: {
  error: string | null;
  hasData: boolean;
  onRetry: () => void;
}) {
  if (!error) {
    return null;
  }
  return (
    <InlineNotice
      tone={hasData ? "warning" : "error"}
      message={hasData ? `本次更新失败，继续显示上次数据：${error}` : `行情数据加载失败：${error}`}
      action={{ label: "重试", onClick: onRetry }}
    />
  );
}

export function MarketTab() {
  const themeCacheKey = buildClientCacheKey("market-theme-boards");
  const usCacheKey = buildClientCacheKey("market-us-overview");
  const cnIndexCacheKey = buildClientCacheKey("market-cn-index-overview");

  const {
    data: themeData,
    loading: themeLoading,
    revalidating: themeRevalidating,
    error: themeError,
    refresh: refreshTheme,
  } = useCachedFetch({
    cacheKey: themeCacheKey,
    staleTimeMs: 0,
    storage: "none",
    fetcher: () => fetchMarketThemeBoards({ sort: "change" }),
    keepPreviousUnless: acceptMarketThemeBoardFresh,
  });

  const {
    data: usData,
    loading: usLoading,
    error: usError,
    refresh: refreshUs,
  } = useCachedFetch({
    cacheKey: usCacheKey,
    staleTimeMs: 0,
    storage: "none",
    fetcher: () => fetchUsMarketOverview(),
    keepPreviousUnless: acceptUsMarketFresh,
  });

  const {
    data: cnIndexData,
    loading: cnIndexLoading,
    error: cnIndexError,
    refresh: refreshCnIndex,
  } = useCachedFetch({
    cacheKey: cnIndexCacheKey,
    staleTimeMs: 0,
    storage: "none",
    fetcher: () => fetchCnIndexOverview(),
    keepPreviousUnless: acceptCnIndexFresh,
  });

  useEffect(() => {
    deleteClientCache(themeCacheKey, "session");
    deleteClientCache(usCacheKey, "session");
    deleteClientCache(cnIndexCacheKey, "session");
  }, [themeCacheKey, usCacheKey, cnIndexCacheKey]);

  useEffect(() => {
    const usIntervalMs = usRefreshIntervalMs(usData?.session_kind);
    const cnIntervalMs = cnIndexRefreshIntervalMs(cnIndexData?.session_kind);
    let cancelled = false;
    let usTimer: number | null = null;
    let cnTimer: number | null = null;

    const clearTimer = (timer: number | null) => {
      if (timer != null) {
        window.clearInterval(timer);
        window.clearTimeout(timer);
      }
    };

    const armClosedWake = (setTimer: (id: number) => void, nextOpen: () => number, refresh: () => void) => {
      setTimer(
        window.setTimeout(() => {
          void refresh();
          if (cancelled) {
            return;
          }
          armClosedWake(setTimer, nextOpen, refresh);
        }, Math.max(60_000, nextOpen())),
      );
    };

    const start = () => {
      if (usTimer == null) {
        if (usIntervalMs != null) {
          usTimer = window.setInterval(() => {
            void refreshUs();
          }, usIntervalMs);
        } else {
          armClosedWake((id) => {
            usTimer = id;
          }, () => msUntilNextWeekdayWallClock("America/New_York", 4, 0), refreshUs);
        }
      }
      if (cnTimer == null) {
        if (cnIntervalMs != null) {
          cnTimer = window.setInterval(() => {
            void refreshCnIndex();
          }, cnIntervalMs);
        } else {
          armClosedWake((id) => {
            cnTimer = id;
          }, () => msUntilNextWeekdayWallClock("Asia/Shanghai", 9, 30), refreshCnIndex);
        }
      }
    };
    const stop = () => {
      clearTimer(usTimer);
      clearTimer(cnTimer);
      usTimer = null;
      cnTimer = null;
    };
    const handleVisibility = () => {
      if (document.hidden) {
        stop();
        return;
      }
      if (usIntervalMs != null) {
        void refreshUs();
      }
      if (cnIntervalMs != null) {
        void refreshCnIndex();
      }
      start();
    };
    if (!document.hidden) {
      start();
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [cnIndexData?.session_kind, usData?.session_kind, refreshUs, refreshCnIndex]);

  return (
    <div className="market-workspace mx-auto grid max-w-5xl gap-4">
      <MarketFetchNotice
        error={cnIndexError}
        hasData={isCnIndexOverviewUsable(cnIndexData)}
        onRetry={() => void refreshCnIndex()}
      />
      <MarketIndexStrip
        cnData={cnIndexData}
        usData={usData}
        cnLoading={cnIndexLoading}
        usLoading={usLoading}
      />

      <MarketFetchNotice
        error={usError}
        hasData={usData != null}
        onRetry={() => void refreshUs()}
      />

      <FundReturnDistributionPanel />
      <MarketFetchNotice
        error={themeError}
        hasData={isMarketThemeBoardUsable(themeData)}
        onRetry={() => void refreshTheme()}
      />
      {!themeError || isMarketThemeBoardUsable(themeData) ? (
        <ThemeSectorOverview
          data={themeData}
          loading={themeLoading && !isMarketThemeBoardUsable(themeData)}
          revalidating={themeRevalidating}
        />
      ) : null}
    </div>
  );
}
