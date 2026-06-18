"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  MarketBoardSort,
  MarketBoardType,
  UsSessionKind,
} from "@/lib/api";
import {
  fetchMarketSectorBoardList,
  fetchMarketSectorBoardWidget,
  fetchMarketThemeBoards,
  fetchUsMarketOverview,
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
import { acceptUsMarketFresh, usRefreshIntervalMs } from "@/lib/usMarketOverview";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { HotSectorList } from "@/components/HotSectorList";
import { SectorPerformanceCard } from "@/components/SectorPerformanceCard";
import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { UsMarketOverview } from "@/components/UsMarketOverview";

export function MarketTab() {
  const [subTab, setSubTab] = useState<MarketSubTab>(() => loadMarketSubTab());
  const [metric, setMetric] = useState<"change" | "inflow">("change");
  const [boardType, setBoardType] = useState<MarketBoardType>("industry");
  const [sort, setSort] = useState<MarketBoardSort>("change");
  // 美股快照的时段类型独立追踪：用作 staleTimeMs/刷新间隔的输入，
  // 避免在 useCachedFetch 选项里前向引用其自身返回的 data。
  const [usSessionKind, setUsSessionKind] = useState<UsSessionKind>("closed");
  const listRef = useRef<HTMLDivElement>(null);
  const forceListRefreshRef = useRef(false);
  const forceThemeRefreshRef = useRef(false);

  const widgetCacheKey = buildClientCacheKey("market-sector-boards-widget");
  const listCacheKey = buildClientCacheKey("market-sector-boards-list", boardType, sort);
  const themeCacheKey = buildClientCacheKey("market-theme-boards", "change");
  const usCacheKey = buildClientCacheKey("market-us-overview");

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

  // 同步最新快照的时段，驱动 staleTimeMs 与下方自动刷新间隔。
  useEffect(() => {
    if (usData?.session_kind && usData.session_kind !== usSessionKind) {
      setUsSessionKind(usData.session_kind);
    }
  }, [usData?.session_kind, usSessionKind]);

  // 时段感知自动刷新：仅在「美股」子 Tab 且页面可见时运行，
  // subTab 切走或 document.hidden 时清除定时器（Req 5.1/5.2/5.3）。
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
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={subTab === "us"}
          onClick={() => handleSubTabChange("us")}
        >
          美股
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
      ) : subTab === "themes" ? (
        <ThemeSectorOverview
          data={themeData}
          loading={themeLoading && !isMarketThemeBoardUsable(themeData)}
          revalidating={themeRevalidating}
          onRefresh={handleRefreshTheme}
        />
      ) : (
        <UsMarketOverview data={usData} loading={usLoading} revalidating={usRevalidating} />
      )}

      {subTab !== "us" && footerDate ? (
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
