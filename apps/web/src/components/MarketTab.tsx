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
import { acceptUsMarketFresh, usRefreshIntervalMs } from "@/lib/usMarketOverview";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { UsMarketOverview } from "@/components/UsMarketOverview";

export function MarketTab() {
  const [subTab, setSubTab] = useState<MarketSubTab>(() => loadMarketSubTab());
  const [usSessionKind, setUsSessionKind] = useState<UsSessionKind>("closed");
  const forceThemeRefreshRef = useRef(false);

  const themeCacheKey = buildClientCacheKey("market-theme-boards");
  const usCacheKey = buildClientCacheKey("market-us-overview");

  const {
    data: themeData,
    loading: themeLoading,
    revalidating: themeRevalidating,
    refresh: refreshTheme,
  } = useCachedFetch({
    cacheKey: themeCacheKey,
    staleTimeMs: 900_000,
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
    <div className="grid gap-4">
      <TradingSessionBar />

      <div className="tab-segment">
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

      {subTab === "themes" ? (
        <ThemeSectorOverview
          data={themeData}
          loading={themeLoading && !isMarketThemeBoardUsable(themeData)}
          revalidating={themeRevalidating}
          onRefresh={handleRefreshTheme}
        />
      ) : (
        <UsMarketOverview data={usData} loading={usLoading} revalidating={usRevalidating} />
      )}

      {subTab === "themes" && footerDate ? (
        <p className="text-center text-xs text-slate-400">
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
