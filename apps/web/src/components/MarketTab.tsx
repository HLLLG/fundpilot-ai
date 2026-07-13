"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { UsSessionKind } from "@/lib/api";
import { fetchMarketThemeBoards, fetchUsMarketOverview } from "@/lib/api";
import { buildClientCacheKey } from "@/lib/clientCache";
import {
  acceptMarketThemeBoardFresh,
  isMarketThemeBoardUsable,
  loadMarketSubTab,
  saveMarketSubTab,
  type MarketSubTab,
} from "@/lib/marketThemeBoard";
import {
  DISCOVERY_FOCUS_CHANGED_EVENT,
  loadDiscoveryFocusSectors,
  toggleDiscoveryFocusSector,
} from "@/lib/discoveryFocusSectors";
import { acceptUsMarketFresh, usRefreshIntervalMs } from "@/lib/usMarketOverview";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { MarketBreadthGauge } from "@/components/MarketBreadthGauge";
import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { UsMarketOverview } from "@/components/UsMarketOverview";
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
      message={hasData ? `本次更新失败，继续显示上次数据：${error}` : `市场数据加载失败：${error}`}
      action={{ label: "重试", onClick: onRetry }}
    />
  );
}

export function MarketTab() {
  const [subTab, setSubTab] = useState<MarketSubTab>(() => loadMarketSubTab());
  const [usSessionKind, setUsSessionKind] = useState<UsSessionKind>("closed");
  const [focusSectors, setFocusSectors] = useState<string[]>(() => loadDiscoveryFocusSectors());
  const forceThemeRefreshRef = useRef(false);

  const themeCacheKey = buildClientCacheKey("market-theme-boards");
  const usCacheKey = buildClientCacheKey("market-us-overview");

  const {
    data: themeData,
    loading: themeLoading,
    revalidating: themeRevalidating,
    error: themeError,
    refresh: refreshTheme,
  } = useCachedFetch({
    cacheKey: themeCacheKey,
    staleTimeMs: 1_200_000,
    storage: "session",
    fetcher: () =>
      fetchMarketThemeBoards({
        sort: "change",
        forceRefresh: forceThemeRefreshRef.current,
      }),
    keepPreviousUnless: acceptMarketThemeBoardFresh,
    enabled: subTab === "themes",
  });

  const {
    data: usData,
    loading: usLoading,
    revalidating: usRevalidating,
    error: usError,
    refresh: refreshUs,
  } = useCachedFetch({
    cacheKey: usCacheKey,
    staleTimeMs: usRefreshIntervalMs(usSessionKind),
    storage: "session",
    fetcher: () => fetchUsMarketOverview(),
    keepPreviousUnless: acceptUsMarketFresh,
    enabled: subTab === "us",
  });

  useEffect(() => {
    if (usData?.session_kind && usData.session_kind !== usSessionKind) {
      setUsSessionKind(usData.session_kind);
    }
  }, [usData?.session_kind, usSessionKind]);

  useEffect(() => {
    const onFocusChanged = (event: Event) => {
      setFocusSectors((event as CustomEvent<string[]>).detail);
    };
    window.addEventListener(DISCOVERY_FOCUS_CHANGED_EVENT, onFocusChanged);
    return () => window.removeEventListener(DISCOVERY_FOCUS_CHANGED_EVENT, onFocusChanged);
  }, []);

  useEffect(() => {
    if (subTab !== "us") {
      return;
    }
    const intervalMs = usRefreshIntervalMs(usData?.session_kind ?? "closed");
    let timer: number | null = null;
    const start = () => {
      if (timer == null) {
        timer = window.setInterval(() => {
          void refreshUs();
        }, intervalMs);
      }
    };
    const stop = () => {
      if (timer != null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const handleVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        start();
      }
    };
    if (!document.hidden) {
      start();
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [subTab, usData?.session_kind, refreshUs]);

  const handleSubTabChange = useCallback((next: MarketSubTab) => {
    setSubTab(next);
    saveMarketSubTab(next);
  }, []);

  const handleToggleFocusSector = useCallback((sectorLabel: string) => {
    toggleDiscoveryFocusSector(sectorLabel);
  }, []);

  const handleRefreshTheme = useCallback(() => {
    forceThemeRefreshRef.current = true;
    void refreshTheme().finally(() => {
      forceThemeRefreshRef.current = false;
    });
  }, [refreshTheme]);

  const footerDate = themeData?.trade_date;
  const footerRevalidating = themeRevalidating;
  const footerStale = themeData?.stale;
  const footerFromCache = themeData?.from_cache;

  return (
    <div className="market-workspace mx-auto grid max-w-5xl gap-4">
      <TradingSessionBar />

      <div className="market-nav overflow-hidden">
      <div className="tab-segment !border-0 !bg-transparent">
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={subTab === "themes"}
          onClick={() => handleSubTabChange("themes")}
        >
          主题板块
        </button>
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={subTab === "us"}
          onClick={() => handleSubTabChange("us")}
        >
          美股
        </button>
      </div>
      </div>

      {subTab === "themes" ? (
        <>
          <MarketBreadthGauge />
          <MarketFetchNotice
            error={themeError}
            hasData={isMarketThemeBoardUsable(themeData)}
            onRetry={handleRefreshTheme}
          />
          {!themeError || isMarketThemeBoardUsable(themeData) ? (
            <ThemeSectorOverview
              data={themeData}
              loading={themeLoading && !isMarketThemeBoardUsable(themeData)}
              revalidating={themeRevalidating}
              onRefresh={handleRefreshTheme}
              onAddFocusSector={handleToggleFocusSector}
              focusSectors={focusSectors}
            />
          ) : null}
        </>
      ) : null}

      {subTab === "us" ? (
        <>
          <MarketFetchNotice
            error={usError}
            hasData={usData != null}
            onRetry={() => void refreshUs()}
          />
          {!usError || usData != null ? (
            <UsMarketOverview data={usData} loading={usLoading} revalidating={usRevalidating} />
          ) : null}
        </>
      ) : null}

      {subTab === "themes" && footerDate ? (
        <p className="mt-2 pb-2 text-center text-xs text-slate-500 lg:pb-0">
          数据日期 {footerDate}
          {footerRevalidating
            ? " · 更新中…"
            : footerStale
              ? " · 上次缓存"
              : footerFromCache
                ? " · 缓存"
                : ""}
        </p>
      ) : null}

    </div>
  );
}
