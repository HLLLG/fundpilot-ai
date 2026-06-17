"use client";

import { useCallback, useRef, useState } from "react";
import type { MarketBoardSort, MarketBoardType, MarketThemeBoardSort } from "@/lib/api";
import {
  fetchMarketSectorBoardList,
  fetchMarketSectorBoardWidget,
  fetchMarketThemeBoards,
} from "@/lib/api";
import { buildClientCacheKey } from "@/lib/clientCache";
import {
  acceptMarketListFresh,
  acceptMarketWidgetFresh,
  isMarketListUsable,
  isMarketWidgetUsable,
} from "@/lib/marketSectorBoard";
import {
  acceptMarketThemeBoardFresh,
  isMarketThemeBoardUsable,
  loadMarketSubTab,
  saveMarketSubTab,
  type MarketSubTab,
} from "@/lib/marketThemeBoard";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { HotSectorList } from "@/components/HotSectorList";
import { SectorPerformanceCard } from "@/components/SectorPerformanceCard";
import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import { TradingSessionBar } from "@/components/TradingSessionBar";

export function MarketTab() {
  const [subTab, setSubTab] = useState<MarketSubTab>(() => loadMarketSubTab());
  const [metric, setMetric] = useState<"change" | "inflow">("change");
  const [boardType, setBoardType] = useState<MarketBoardType>("industry");
  const [sort, setSort] = useState<MarketBoardSort>("change");
  const [themeSort, setThemeSort] = useState<MarketThemeBoardSort>("change");
  const listRef = useRef<HTMLDivElement>(null);
  const forceListRefreshRef = useRef(false);
  const forceThemeRefreshRef = useRef(false);

  const widgetCacheKey = buildClientCacheKey("market-sector-boards-widget");
  const listCacheKey = buildClientCacheKey("market-sector-boards-list", boardType, sort);
  const themeCacheKey = buildClientCacheKey("market-theme-boards", themeSort);

  const {
    data: widgetData,
    loading: widgetLoading,
    revalidating: widgetRevalidating,
  } = useCachedFetch({
    cacheKey: widgetCacheKey,
    staleTimeMs: 60_000,
    storage: "session",
    fetcher: () => fetchMarketSectorBoardWidget(),
    keepPreviousUnless: acceptMarketWidgetFresh,
    enabled: subTab === "market",
  });

  const {
    data: listData,
    loading: listLoading,
    revalidating: listRevalidating,
    refresh: refreshList,
  } = useCachedFetch({
    cacheKey: listCacheKey,
    staleTimeMs: 60_000,
    storage: "session",
    fetcher: () =>
      fetchMarketSectorBoardList({
        boardType,
        sort,
        forceRefresh: forceListRefreshRef.current,
      }),
    keepPreviousUnless: acceptMarketListFresh,
    enabled: subTab === "market",
  });

  const {
    data: themeData,
    loading: themeLoading,
    revalidating: themeRevalidating,
    refresh: refreshTheme,
  } = useCachedFetch({
    cacheKey: themeCacheKey,
    staleTimeMs: 60_000,
    storage: "session",
    fetcher: () =>
      fetchMarketThemeBoards({
        sort: themeSort,
        forceRefresh: forceThemeRefreshRef.current,
      }),
    keepPreviousUnless: acceptMarketThemeBoardFresh,
    enabled: subTab === "themes",
  });

  const handleSubTabChange = useCallback((next: MarketSubTab) => {
    setSubTab(next);
    saveMarketSubTab(next);
  }, []);

  const scrollToList = useCallback(() => {
    listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const handleRefreshList = useCallback(() => {
    forceListRefreshRef.current = true;
    void refreshList().finally(() => {
      forceListRefreshRef.current = false;
    });
  }, [refreshList]);

  const handleRefreshTheme = useCallback(() => {
    forceThemeRefreshRef.current = true;
    void refreshTheme().finally(() => {
      forceThemeRefreshRef.current = false;
    });
  }, [refreshTheme]);

  const footerDate = subTab === "market" ? widgetData?.trade_date : themeData?.trade_date;
  const footerRevalidating = subTab === "market" ? widgetRevalidating || listRevalidating : themeRevalidating;
  const footerStale = subTab === "market" ? widgetData?.stale : themeData?.stale;
  const footerFromCache = subTab === "market" ? widgetData?.from_cache : themeData?.from_cache;

  return (
    <div className="grid gap-4">
      <TradingSessionBar />

      <div className="tab-segment">
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={subTab === "market"}
          onClick={() => handleSubTabChange("market")}
        >
          全市场
        </button>
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={subTab === "themes"}
          onClick={() => handleSubTabChange("themes")}
        >
          主题板块
        </button>
      </div>

      {subTab === "market" ? (
        <>
          <SectorPerformanceCard
            data={widgetData}
            loading={widgetLoading && !isMarketWidgetUsable(widgetData)}
            revalidating={widgetRevalidating}
            metric={metric}
            onMetricChange={setMetric}
            onOpenDetail={scrollToList}
          />
          <div ref={listRef}>
            <HotSectorList
              data={listData}
              loading={listLoading && !isMarketListUsable(listData)}
              revalidating={listRevalidating}
              boardType={boardType}
              sort={sort}
              onBoardTypeChange={setBoardType}
              onSortChange={setSort}
              onRefresh={handleRefreshList}
            />
          </div>
        </>
      ) : (
        <ThemeSectorOverview
          data={themeData}
          loading={themeLoading && !isMarketThemeBoardUsable(themeData)}
          revalidating={themeRevalidating}
          sort={themeSort}
          onSortChange={setThemeSort}
          onRefresh={handleRefreshTheme}
        />
      )}

      {footerDate ? (
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
