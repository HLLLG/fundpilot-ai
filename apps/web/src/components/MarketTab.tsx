"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Holding, UsSessionKind } from "@/lib/api";
import { fetchDipRadar, fetchMarketThemeBoards, fetchUsMarketOverview } from "@/lib/api";
import { buildClientCacheKey } from "@/lib/clientCache";
import { acceptDipRadarFresh } from "@/lib/dipRadar";
import {
  acceptMarketThemeBoardFresh,
  isMarketThemeBoardUsable,
  loadDipRadarSectorFilter,
  loadDiscoveryFocusSectors,
  loadMarketSubTab,
  saveDipRadarSectorFilter,
  saveMarketSubTab,
  toggleDiscoveryFocusSector,
  type MarketSubTab,
} from "@/lib/marketThemeBoard";
import { DISCOVERY_FOCUS_CHANGED_EVENT } from "@/lib/discoveryFocusSectors";
import { acceptUsMarketFresh, usRefreshIntervalMs } from "@/lib/usMarketOverview";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { DipReboundRadar } from "@/components/DipReboundRadar";
import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { UsMarketOverview } from "@/components/UsMarketOverview";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";

export function MarketTab() {
  const [subTab, setSubTab] = useState<MarketSubTab>(() => loadMarketSubTab());
  const [usSessionKind, setUsSessionKind] = useState<UsSessionKind>("closed");
  const [dipLookbackDays, setDipLookbackDays] = useState<3 | 5>(5);
  const [dipSectorFilter, setDipSectorFilter] = useState<string | null>(() => loadDipRadarSectorFilter());
  const [focusSectors, setFocusSectors] = useState<string[]>(() => loadDiscoveryFocusSectors());
  const [previewHolding, setPreviewHolding] = useState<Holding | null>(null);
  const forceThemeRefreshRef = useRef(false);
  const forceDipRefreshRef = useRef(false);

  const themeCacheKey = buildClientCacheKey("market-theme-boards");
  const usCacheKey = buildClientCacheKey("market-us-overview");
  const dipCacheKey = buildClientCacheKey(`market-dip-radar:${dipLookbackDays}:${dipSectorFilter ?? "all"}`);

  const {
    data: themeData,
    loading: themeLoading,
    revalidating: themeRevalidating,
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
    refresh: refreshUs,
  } = useCachedFetch({
    cacheKey: usCacheKey,
    staleTimeMs: usRefreshIntervalMs(usSessionKind),
    storage: "session",
    fetcher: () => fetchUsMarketOverview(),
    keepPreviousUnless: acceptUsMarketFresh,
    enabled: subTab === "us",
  });

  const {
    data: dipData,
    loading: dipLoading,
    revalidating: dipRevalidating,
    refresh: refreshDip,
  } = useCachedFetch({
    cacheKey: dipCacheKey,
    staleTimeMs: 1_200_000,
    storage: "session",
    fetcher: () =>
      fetchDipRadar({
        lookbackDays: dipLookbackDays,
        sector: dipSectorFilter,
        forceRefresh: forceDipRefreshRef.current,
      }),
    keepPreviousUnless: acceptDipRadarFresh,
    enabled: subTab === "dip_radar",
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

  const handleViewDipFunds = useCallback(
    (sectorLabel: string) => {
      saveDipRadarSectorFilter(sectorLabel);
      setDipSectorFilter(sectorLabel);
      handleSubTabChange("dip_radar");
    },
    [handleSubTabChange],
  );

  const handleToggleFocusSector = useCallback((sectorLabel: string) => {
    toggleDiscoveryFocusSector(sectorLabel);
  }, []);

  const handleDipSectorFilterChange = useCallback((sector: string | null) => {
    setDipSectorFilter(sector);
    if (sector) {
      saveDipRadarSectorFilter(sector);
    } else {
      saveDipRadarSectorFilter("");
    }
  }, []);

  const handleRefreshTheme = useCallback(() => {
    forceThemeRefreshRef.current = true;
    void refreshTheme().finally(() => {
      forceThemeRefreshRef.current = false;
    });
  }, [refreshTheme]);

  const handleRefreshDip = useCallback(() => {
    forceDipRefreshRef.current = true;
    void refreshDip().finally(() => {
      forceDipRefreshRef.current = false;
    });
  }, [refreshDip]);

  const handleOpenFund = useCallback((fundCode: string, fundName: string) => {
    setPreviewHolding({
      fund_code: fundCode,
      fund_name: fundName,
      holding_amount: 0,
      return_percent: 0,
    });
  }, []);

  const footerDate = themeData?.trade_date;
  const footerRevalidating = themeRevalidating;
  const footerStale = themeData?.stale;
  const footerFromCache = themeData?.from_cache;

  return (
    <div className="mx-auto grid max-w-3xl gap-4">
      <TradingSessionBar />

      <div className="section-card overflow-hidden p-1">
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
          aria-pressed={subTab === "dip_radar"}
          onClick={() => handleSubTabChange("dip_radar")}
        >
          大跌雷达
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
        <ThemeSectorOverview
          data={themeData}
          loading={themeLoading && !isMarketThemeBoardUsable(themeData)}
          revalidating={themeRevalidating}
          onRefresh={handleRefreshTheme}
          onViewDipFunds={handleViewDipFunds}
          onAddFocusSector={handleToggleFocusSector}
          focusSectors={focusSectors}
        />
      ) : null}

      {subTab === "dip_radar" ? (
        <DipReboundRadar
          data={dipData}
          loading={dipLoading && dipData == null}
          revalidating={dipRevalidating}
          lookbackDays={dipLookbackDays}
          onLookbackDaysChange={setDipLookbackDays}
          sectorFilter={dipSectorFilter}
          onSectorFilterChange={handleDipSectorFilterChange}
          onRefresh={handleRefreshDip}
          onOpenFund={handleOpenFund}
        />
      ) : null}

      {subTab === "us" ? (
        <UsMarketOverview data={usData} loading={usLoading} revalidating={usRevalidating} />
      ) : null}

      {subTab === "themes" && footerDate ? (
        <p className="mt-2 pb-2 text-center text-xs text-slate-400 lg:pb-0">
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

      {previewHolding ? (
        <YangjibaoFundDetail
          holding={previewHolding}
          holdingIndex={0}
          holdings={[previewHolding]}
          onClose={() => setPreviewHolding(null)}
          onNavigate={() => undefined}
        />
      ) : null}
    </div>
  );
}
