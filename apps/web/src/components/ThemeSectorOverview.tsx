"use client";

import { Fragment, useEffect, useId, useMemo, useState } from "react";
import { Briefcase, ChevronDown, ChevronRight, ChevronUp, Loader2, Search, X } from "lucide-react";
import { BoardFlowHistoryChart } from "@/components/BoardFlowHistoryChart";
import type {
  BoardFlowHistoryRange,
  BoardFlowHistoryResponse,
  MarketThemeBoardItem,
  MarketThemeBoardResponse,
} from "@/lib/api";
import { fetchBoardFlowHistory } from "@/lib/api";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { MethodologyNote } from "@/components/MethodologyNote";
import {
  boardKindClass,
  countHeldThemeBoards,
  filterThemeBoardItems,
  formatBoardKindLabel,
  formatThemeBoardUpdatedAt,
  formatThemeFlowYi,
  formatThemePercent,
  formatThemeRank,
  formatThemeStreak,
  hasThemeFlowDetail,
  isMarketThemeBoardUsable,
  nextThemeSortState,
  profitToneClass,
  sortThemeBoardItems,
  THEME_FLOW_TIER_ROWS,
  themeBoardHeading,
  themeRankClass,
  type ThemeSortColumn,
  type ThemeSortDirection,
} from "@/lib/marketThemeBoard";

type ThemeSectorOverviewProps = {
  data: MarketThemeBoardResponse | null;
  loading: boolean;
  revalidating?: boolean;
};

const FLOW_HISTORY_LOAD_FAILED = "历史资金流加载失败";
const MOBILE_PAGE_SIZE = 10;
const DESKTOP_QUERY = "(min-width: 640px)";

function flowHistoryCacheId(label: string, boardCode?: string | null) {
  return `${label}:${boardCode ?? ""}`;
}

function FlowTierGrid({ item }: { item: MarketThemeBoardItem }) {
  const tiers = item.flow_tiers;
  if (!tiers) {
    return null;
  }
  // 指数主题的榜面涨幅是跟踪指数口径，与资金流的东财板块不是同一个成分篮子；
  // 展开资金详情时补一行板块口径自身涨跌，用户对照资金数据才不会拿错价格。
  const flowBasketDiffers =
    Boolean(item.flow_source_code) &&
    Boolean(item.source_code) &&
    item.flow_source_code !== item.source_code;
  const showFlowChange = flowBasketDiffers && item.flow_change_1d_percent != null;
  return (
    <div className="mt-2 space-y-2 border-t border-slate-100 pt-2">
      {showFlowChange ? (
        <div className="flex items-center justify-between rounded-lg bg-white/80 px-2.5 py-2 text-xs">
          <span className="text-slate-500">资金口径板块今日（东财 {item.flow_source_code}）</span>
          <span className={`tabular-nums font-medium ${profitToneClass(item.flow_change_1d_percent)}`}>
            {formatThemePercent(item.flow_change_1d_percent)}
          </span>
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-2 text-xs">
        {THEME_FLOW_TIER_ROWS.map(({ key, label, hint }) => {
          const value = tiers[key];
          return (
            <div key={key} className="rounded-lg bg-white/80 px-2.5 py-2">
              <div className="text-slate-500">
                {label}
                {hint ? <span className="text-[10px]">（{hint}）</span> : null}
              </div>
              <div className={`mt-0.5 tabular-nums font-medium ${profitToneClass(value)}`}>
                {formatThemeFlowYi(value)}
              </div>
            </div>
          );
        })}
      </div>
      <MethodologyNote>
        主力净流入 = 超大单 + 大单。
        {flowBasketDiffers
          ? "榜面涨幅为跟踪指数口径（与基金净值对齐）；资金及量价信号取自东财板块口径，并在该口径内同源计算。"
          : "涨幅与资金均为东财板块口径（同源）。"}
      </MethodologyNote>
    </div>
  );
}

function FlowHistoryPanel({
  flowRange,
  onRangeChange,
  cached,
  loading,
  onRetry,
}: {
  flowRange: BoardFlowHistoryRange;
  onRangeChange: (range: BoardFlowHistoryRange) => void;
  cached: BoardFlowHistoryResponse | null | undefined;
  loading: boolean;
  onRetry: () => void;
}) {
  const retryable = cached?.message === FLOW_HISTORY_LOAD_FAILED;
  return (
    <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-slate-600">主力净流入走势</p>
        <div
          className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 text-xs"
          role="group"
          aria-label="资金流时间范围"
        >
          {(["week", "month"] as const).map((range) => (
            <button
              key={range}
              type="button"
              onClick={() => onRangeChange(range)}
              aria-pressed={flowRange === range}
              className={`min-h-11 rounded-md px-3 py-1 font-medium transition-colors ${
                flowRange === range
                  ? "bg-[var(--brand-soft)] text-[var(--brand-strong)]"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {range === "week" ? "近一周" : "近一月"}
            </button>
          ))}
        </div>
      </div>
      {loading && !cached ? (
        <div className="flex items-center justify-center py-8 text-xs text-slate-500">
          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
          加载历史资金流…
        </div>
      ) : cached?.available ? (
        <BoardFlowHistoryChart
          points={cached.points}
          cumulativeNetYi={cached.cumulative_net_yi}
        />
      ) : retryable ? (
        <div className="py-4 text-center text-xs text-slate-500">
          <p>{cached.message}</p>
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 min-h-11 rounded-lg border border-slate-200 bg-white px-4 font-semibold text-[var(--brand-strong)] transition hover:border-[var(--brand)] hover:bg-[var(--brand-soft)]"
          >
            重试
          </button>
        </div>
      ) : (
        <p className="py-6 text-center text-xs text-slate-500">
          {cached?.message ?? "暂无历史资金流数据"}
        </p>
      )}
    </div>
  );
}

function SortColumnHeader({
  label,
  column,
  activeColumn,
  direction,
  onSort,
}: {
  label: string;
  column: ThemeSortColumn;
  activeColumn: ThemeSortColumn;
  direction: ThemeSortDirection;
  onSort: (column: ThemeSortColumn) => void;
}) {
  const active = activeColumn === column;
  return (
    <button
      type="button"
      onClick={() => onSort(column)}
      className={`inline-flex min-h-11 min-w-11 w-full items-center justify-end gap-0.5 font-medium transition-colors ${
        active ? "text-slate-700" : "text-slate-500 hover:text-slate-600"
      }`}
      aria-label={`按${label}${active ? (direction === "desc" ? "从大到小" : "从小到大") : "从大到小"}排序`}
    >
      <span>{label}</span>
      <span className="inline-flex flex-col leading-none" aria-hidden>
        <ChevronUp
          className={`h-2.5 w-2.5 ${active && direction === "asc" ? "text-slate-800" : "text-slate-300"}`}
          strokeWidth={2.5}
        />
        <ChevronDown
          className={`-mt-1 h-2.5 w-2.5 ${active && direction === "desc" ? "text-slate-800" : "text-slate-300"}`}
          strokeWidth={2.5}
        />
      </span>
    </button>
  );
}

function sortAriaValue(
  column: ThemeSortColumn,
  activeColumn: ThemeSortColumn,
  direction: ThemeSortDirection,
): "ascending" | "descending" | "none" {
  if (column !== activeColumn) {
    return "none";
  }
  return direction === "asc" ? "ascending" : "descending";
}

function ThemeItemBadges({
  item,
  showUnheld = false,
}: {
  item: MarketThemeBoardItem;
  showUnheld?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${boardKindClass(item.board_kind)}`}>
        {formatBoardKindLabel(item.board_kind)}
      </span>
      {item.in_portfolio ? (
        <span className="rounded bg-[var(--info-bg)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--info-fg)]">
          持仓{item.held_fund_count > 0 ? ` ${item.held_fund_count} 只` : ""}
        </span>
      ) : showUnheld ? (
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
          未持仓
        </span>
      ) : null}
    </div>
  );
}

function ThemeExpandButton({
  item,
  expanded,
  detailsId,
  onToggle,
  showLabel = false,
}: {
  item: MarketThemeBoardItem;
  expanded: boolean;
  detailsId: string;
  onToggle: () => void;
  showLabel?: boolean;
}) {
  const action = expanded ? "收起" : "展开";
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-controls={detailsId}
      aria-label={`${action}${item.sector_label}资金详情`}
      className={`inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-slate-600 transition hover:border-[var(--brand)] hover:text-[var(--brand-strong)] ${
        showLabel ? "w-full px-2" : "h-11 w-11"
      }`}
    >
      {expanded ? <ChevronDown className="h-4 w-4" aria-hidden /> : <ChevronRight className="h-4 w-4" aria-hidden />}
      {showLabel ? "资金详情" : null}
    </button>
  );
}

function ThemeExpandedDetails({
  id,
  item,
  flowRange,
  onRangeChange,
  cached,
  loading,
  onRetry,
  mobile = false,
}: {
  id: string;
  item: MarketThemeBoardItem;
  flowRange: BoardFlowHistoryRange;
  onRangeChange: (range: BoardFlowHistoryRange) => void;
  cached: BoardFlowHistoryResponse | null | undefined;
  loading: boolean;
  onRetry: () => void;
  mobile?: boolean;
}) {
  return (
    <div id={id} className={mobile ? "mt-3 rounded-xl bg-slate-50 p-3" : undefined}>
      {item.flow_tiers ? <FlowTierGrid item={item} /> : null}
      <FlowHistoryPanel
        flowRange={flowRange}
        onRangeChange={onRangeChange}
        cached={cached}
        loading={loading}
        onRetry={onRetry}
      />
    </div>
  );
}

export function ThemeSectorOverview({
  data,
  loading,
  revalidating = false,
}: ThemeSectorOverviewProps) {
  const detailsIdPrefix = useId().replace(/:/g, "");
  const [expandedItem, setExpandedItem] = useState<{
    label: string;
    boardCode?: string | null;
  } | null>(null);
  const [flowRange, setFlowRange] = useState<BoardFlowHistoryRange>("week");
  const [flowCache, setFlowCache] = useState<Record<string, Partial<Record<BoardFlowHistoryRange, BoardFlowHistoryResponse>>>>({});
  const [flowLoadingKey, setFlowLoadingKey] = useState<string | null>(null);
  const [sortColumn, setSortColumn] = useState<ThemeSortColumn>("change");
  const [sortDirection, setSortDirection] = useState<ThemeSortDirection>("desc");
  const [searchQuery, setSearchQuery] = useState("");
  const [heldOnly, setHeldOnly] = useState(false);
  const [mobileVisibleCount, setMobileVisibleCount] = useState(MOBILE_PAGE_SIZE);
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const showData = isMarketThemeBoardUsable(data);
  const heldCount = countHeldThemeBoards(data?.items ?? []);
  const updatedAt = formatThemeBoardUpdatedAt(data);

  useEffect(() => {
    if (!expandedItem) {
      return;
    }
    const cacheId = flowHistoryCacheId(expandedItem.label, expandedItem.boardCode);
    if (flowCache[cacheId]?.[flowRange]) {
      return;
    }

    const cacheKey = `${cacheId}:${flowRange}`;
    let cancelled = false;
    setFlowLoadingKey(cacheKey);
    fetchBoardFlowHistory({
      sectorLabel: expandedItem.label,
      boardCode: expandedItem.boardCode,
      range: flowRange,
    })
      .then((response) => {
        if (cancelled) {
          return;
        }
        setFlowCache((current) => ({
          ...current,
          [cacheId]: {
            ...current[cacheId],
            [flowRange]: response,
          },
        }));
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setFlowCache((current) => ({
          ...current,
          [cacheId]: {
            ...current[cacheId],
            [flowRange]: {
              available: false,
              range: flowRange,
              sector_label: expandedItem.label,
              points: [],
              cumulative_net_yi: null,
              message: FLOW_HISTORY_LOAD_FAILED,
            },
          },
        }));
      })
      .finally(() => {
        if (!cancelled) {
          setFlowLoadingKey((current) => (current === cacheKey ? null : current));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [expandedItem, flowRange, flowCache]);

  const sortedItems = useMemo(
    () =>
      sortThemeBoardItems(
        filterThemeBoardItems(data?.items ?? [], {
          query: searchQuery,
          heldOnly,
        }),
        sortColumn,
        sortDirection,
      ),
    [data?.items, heldOnly, searchQuery, sortColumn, sortDirection],
  );
  const mobileItems = sortedItems.slice(0, mobileVisibleCount);
  const filterActive = Boolean(searchQuery.trim()) || heldOnly;

  useEffect(() => {
    setMobileVisibleCount(MOBILE_PAGE_SIZE);
  }, [data?.trade_date, heldOnly, searchQuery, sortColumn, sortDirection]);

  useEffect(() => {
    if (!expandedItem) {
      return;
    }
    if (!sortedItems.some((item) => item.sector_label === expandedItem.label)) {
      setExpandedItem(null);
    }
  }, [expandedItem, sortedItems]);

  const handleSort = (column: ThemeSortColumn) => {
    const next = nextThemeSortState(column, sortColumn, sortDirection);
    setSortColumn(next.column);
    setSortDirection(next.direction);
  };

  const toggleExpand = (item: MarketThemeBoardItem) => {
    if (!hasThemeFlowDetail(item)) {
      return;
    }
    setExpandedItem((current) =>
      current?.label === item.sector_label
        ? null
        : { label: item.sector_label, boardCode: item.flow_source_code },
    );
  };

  const handleRetryFlowHistory = () => {
    if (!expandedItem) {
      return;
    }
    const cacheId = flowHistoryCacheId(expandedItem.label, expandedItem.boardCode);
    const cacheKey = `${cacheId}:${flowRange}`;
    setFlowLoadingKey(cacheKey);
    setFlowCache((current) => {
      const ranges = { ...current[cacheId] };
      delete ranges[flowRange];
      return { ...current, [cacheId]: ranges };
    });
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-[var(--line)] bg-[var(--panel)] shadow-sm">
      <div className="bg-gradient-to-b from-amber-50/90 via-amber-50/30 to-white px-4 pb-3 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-bold tracking-tight text-slate-900 sm:text-xl">{themeBoardHeading()}</h2>
            {/*
              `stale` 与 `available` 是两件事，此前这里把前者写成了后者的文案。
              后端 `available = bool(items)`，取不到行情时才把「行情暂不可用，请稍后重试」
              放进 `message`（下面那行单独渲染）。`stale` 只表示这份快照可能不是最新——
              典型触发是 api 容器刚重启（`snapshot_refreshed_before_process_boot`），
              此时榜单仍有完整数据。说成「不可用」会让用户以为下面的数字不能用。
            */}
            {data?.stale ? (
              <p className="mt-1 text-xs text-slate-500">可能不是最新，正在刷新</p>
            ) : null}
            {data?.message ? (
              <p className="mt-1 text-xs text-[var(--warn-icon)]">{data.message}</p>
            ) : null}
          </div>
          {updatedAt ? (
            <p className="inline-flex shrink-0 items-center gap-1.5 pt-1 text-right text-xs text-slate-400">
              {revalidating ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
              <span>{updatedAt}</span>
            </p>
          ) : null}
        </div>
      </div>

      <div className="px-4 pb-4 pt-1">
        {showData ? (
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-start">
            <label className="relative flex min-h-11 w-full min-w-0 items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 focus-within:border-[var(--brand)] focus-within:bg-white sm:w-72">
              <Search className="h-4 w-4 shrink-0 text-slate-500" aria-hidden />
              <input
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="搜索板块，如 半导体"
                className="min-h-11 min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-500"
                aria-label="按名称搜索主题板块"
                autoComplete="off"
              />
              {searchQuery ? (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="inline-flex min-h-11 min-w-11 items-center justify-center text-slate-500 hover:text-slate-700"
                  aria-label="清除搜索"
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              ) : null}
            </label>
            <button
              type="button"
              onClick={() => setHeldOnly((current) => !current)}
              aria-pressed={heldOnly}
              className={`inline-flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-xl border px-3 text-sm font-semibold transition ${
                heldOnly
                  ? "border-[var(--info-border)] bg-[var(--info-bg)] text-[var(--info-fg)]"
                  : "border-slate-200 bg-white text-slate-600 hover:border-[var(--brand)] hover:text-[var(--brand-strong)]"
              }`}
            >
              <Briefcase className="h-4 w-4" aria-hidden />
              {heldCount > 0 ? `持仓 ${heldCount}` : "持仓板块"}
            </button>
          </div>
        ) : null}
        {showData && filterActive && sortedItems.length > 0 ? (
          <p className="mb-3 text-xs text-slate-500">
            {heldOnly
              ? `正在查看 ${sortedItems.length} 个持仓板块`
              : `找到 ${sortedItems.length} 个板块`}
          </p>
        ) : null}
        {loading && !showData ? (
          <div className="flex items-center justify-center py-10 text-sm text-slate-500">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            加载主题板块…
          </div>
        ) : !showData ? (
          <p className="py-8 text-center text-sm text-slate-500">{data?.message ?? "暂无主题板块数据"}</p>
        ) : sortedItems.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-500" data-testid="theme-sector-empty-filter">
            {heldOnly && !searchQuery.trim()
              ? "持仓还没有对上主题板块"
              : "没有匹配的板块"}
          </p>
        ) : (
          !isDesktop ? (
            <div className="space-y-3" data-testid="theme-sector-mobile-list">
              {mobileItems.map((item, index) => {
                const expandable = hasThemeFlowDetail(item);
                const expanded = expandedItem?.label === item.sector_label;
                const cacheId = flowHistoryCacheId(item.sector_label, item.flow_source_code);
                const detailsId = `${detailsIdPrefix}-mobile-${index}`;
                return (
                  <article
                    key={item.sector_label}
                    data-testid={`theme-sector-mobile-card-${item.sector_label}`}
                    className={`rounded-2xl border p-3 shadow-sm ${
                      item.in_portfolio
                        ? "border-[var(--info-border)] bg-[var(--info-bg)]/80"
                        : "border-[var(--line)] bg-white"
                    }`}
                  >
                    <div className="flex min-w-0 items-start gap-3">
                      <span
                        className={`mt-0.5 shrink-0 text-xs font-bold tabular-nums ${themeRankClass(item.rank, index)}`}
                        aria-label={`排名 ${formatThemeRank(item.rank, index)}`}
                      >
                        {formatThemeRank(item.rank, index)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <h3 className="break-words text-sm font-bold leading-5 text-slate-900">
                          {item.sector_label}
                        </h3>
                        <div className="mt-1.5">
                          <ThemeItemBadges item={item} showUnheld />
                        </div>
                      </div>
                    </div>

                    <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                      <div className="min-w-0 rounded-xl bg-slate-50 px-2 py-2 text-center">
                        <dt className="text-[11px] font-medium text-slate-500">今日</dt>
                        <dd
                          className={`mt-1 break-words text-sm font-bold tabular-nums ${profitToneClass(item.change_1d_percent)}`}
                        >
                          {formatThemePercent(item.change_1d_percent)}
                        </dd>
                      </div>
                      <div className="min-w-0 rounded-xl bg-slate-50 px-2 py-2 text-center">
                        <dt className="text-[11px] font-medium text-slate-500">连涨</dt>
                        <dd
                          className={`mt-1 break-words text-sm font-bold tabular-nums ${profitToneClass(item.consecutive_up_days)}`}
                        >
                          {formatThemeStreak(item.consecutive_up_days)}
                        </dd>
                      </div>
                      <div className="min-w-0 rounded-xl bg-slate-50 px-2 py-2 text-center">
                        <dt className="text-[11px] font-medium text-slate-500">5日</dt>
                        <dd
                          className={`mt-1 break-words text-sm font-bold tabular-nums ${profitToneClass(item.change_5d_percent)}`}
                        >
                          {formatThemePercent(item.change_5d_percent)}
                        </dd>
                      </div>
                      <div className="min-w-0 rounded-xl bg-slate-50 px-2 py-2 text-center">
                        <dt className="text-[11px] font-medium text-slate-500">主力资金</dt>
                        <dd
                          className={`mt-1 break-words text-xs font-bold tabular-nums ${profitToneClass(item.main_force_net_yi)}`}
                        >
                          {formatThemeFlowYi(item.main_force_net_yi)}
                        </dd>
                      </div>
                    </dl>

                    <div className="mt-3">
                      {expandable ? (
                        <ThemeExpandButton
                          item={item}
                          expanded={expanded}
                          detailsId={detailsId}
                          onToggle={() => toggleExpand(item)}
                          showLabel
                        />
                      ) : (
                        <span className="flex min-h-11 items-center justify-center rounded-lg bg-slate-50 px-2 text-center text-xs text-slate-500">
                          暂无明细
                        </span>
                      )}
                    </div>

                    {expanded && expandable ? (
                      <ThemeExpandedDetails
                        id={detailsId}
                        item={item}
                        flowRange={flowRange}
                        onRangeChange={setFlowRange}
                        cached={flowCache[cacheId]?.[flowRange]}
                        loading={flowLoadingKey === `${cacheId}:${flowRange}`}
                        onRetry={handleRetryFlowHistory}
                        mobile
                      />
                    ) : null}
                  </article>
                );
              })}
              {sortedItems.length > MOBILE_PAGE_SIZE ? (
                <button
                  type="button"
                  onClick={() =>
                    setMobileVisibleCount((current) =>
                      current >= sortedItems.length
                        ? MOBILE_PAGE_SIZE
                        : Math.min(sortedItems.length, current + MOBILE_PAGE_SIZE),
                    )
                  }
                  className="flex min-h-11 w-full items-center justify-center rounded-xl border border-[var(--line)] bg-white px-4 text-sm font-bold text-[var(--brand-strong)] shadow-sm hover:bg-[var(--brand-soft)]"
                  aria-expanded={mobileVisibleCount >= sortedItems.length}
                >
                  {mobileVisibleCount >= sortedItems.length
                    ? `收起到前 ${MOBILE_PAGE_SIZE} 个板块`
                    : `显示更多板块（还剩 ${sortedItems.length - mobileVisibleCount} 个）`}
                </button>
              ) : null}
            </div>

          ) : (
            <div className="overflow-x-auto pr-2" data-testid="theme-sector-desktop-table">
              <table className="w-full min-w-[560px] text-sm">
                <caption className="sr-only">主题板块行情，可按今日、5日和主力净流入排序</caption>
                <thead>
                  <tr className="text-xs">
                    <th scope="col" className="w-12 pb-2 text-left font-medium text-slate-500">
                      <span className="sr-only">资金详情</span>
                    </th>
                    <th scope="col" className="pb-2 pr-2 text-left font-medium text-slate-500">
                      排名
                    </th>
                    <th scope="col" className="pb-2 pr-2 text-left font-medium text-slate-500">
                      板块名称
                    </th>
                    <th
                      scope="col"
                      className="pb-2 pr-2 text-right"
                      aria-sort={sortAriaValue("change", sortColumn, sortDirection)}
                    >
                      <SortColumnHeader
                        label="今日"
                        column="change"
                        activeColumn={sortColumn}
                        direction={sortDirection}
                        onSort={handleSort}
                      />
                    </th>
                    <th
                      scope="col"
                      className="pb-2 pr-2 text-right"
                      aria-sort={sortAriaValue("streak", sortColumn, sortDirection)}
                    >
                      <SortColumnHeader
                        label="连涨天数"
                        column="streak"
                        activeColumn={sortColumn}
                        direction={sortDirection}
                        onSort={handleSort}
                      />
                    </th>
                    <th
                      scope="col"
                      className="pb-2 pr-2 text-right"
                      aria-sort={sortAriaValue("change5d", sortColumn, sortDirection)}
                    >
                      <SortColumnHeader
                        label="5日"
                        column="change5d"
                        activeColumn={sortColumn}
                        direction={sortDirection}
                        onSort={handleSort}
                      />
                    </th>
                    <th
                      scope="col"
                      className="pb-2 pr-6 text-right"
                      aria-sort={sortAriaValue("inflow", sortColumn, sortDirection)}
                    >
                      <SortColumnHeader
                        label="主力净流入"
                        column="inflow"
                        activeColumn={sortColumn}
                        direction={sortDirection}
                        onSort={handleSort}
                      />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedItems.map((item, index) => {
                    const expandable = hasThemeFlowDetail(item);
                    const expanded = expandedItem?.label === item.sector_label;
                    const cacheId = flowHistoryCacheId(item.sector_label, item.flow_source_code);
                    const detailsId = `${detailsIdPrefix}-desktop-${index}`;
                    return (
                      <Fragment key={item.sector_label}>
                        <tr
                          className={`border-t border-[var(--line)] first:border-t-0 ${
                            item.in_portfolio ? "bg-[var(--info-bg)]/80" : ""
                          }`}
                        >
                          <td className="py-2 pr-1 text-slate-500">
                            {expandable ? (
                              <ThemeExpandButton
                                item={item}
                                expanded={expanded}
                                detailsId={detailsId}
                                onToggle={() => toggleExpand(item)}
                              />
                            ) : null}
                          </td>
                          <td className={`py-3 pr-2 tabular-nums ${themeRankClass(item.rank, index)}`}>
                            {formatThemeRank(item.rank, index)}
                          </td>
                          <td className="py-3 pr-2">
                            <div className="min-w-0 space-y-1.5">
                              <span className="block break-words font-medium text-slate-900">
                                {item.sector_label}
                              </span>
                              <ThemeItemBadges item={item} />
                            </div>
                          </td>
                          <td
                            className={`py-3 pr-2 text-right tabular-nums font-medium ${profitToneClass(item.change_1d_percent)}`}
                          >
                            {formatThemePercent(item.change_1d_percent)}
                          </td>
                          <td
                            className={`py-3 pr-2 text-right tabular-nums font-medium ${profitToneClass(item.consecutive_up_days)}`}
                          >
                            {formatThemeStreak(item.consecutive_up_days)}
                          </td>
                          <td
                            className={`py-3 pr-2 text-right tabular-nums font-medium ${profitToneClass(item.change_5d_percent)}`}
                          >
                            {formatThemePercent(item.change_5d_percent)}
                          </td>
                          <td
                            className={`py-3 pr-6 text-right tabular-nums font-medium ${profitToneClass(item.main_force_net_yi)}`}
                          >
                            {formatThemeFlowYi(item.main_force_net_yi)}
                          </td>
                        </tr>
                        {expanded && expandable ? (
                          <tr className="bg-slate-50/60">
                            <td colSpan={7} className="px-3 pb-3 pt-0">
                              <ThemeExpandedDetails
                                id={detailsId}
                                item={item}
                                flowRange={flowRange}
                                onRangeChange={setFlowRange}
                                cached={flowCache[cacheId]?.[flowRange]}
                                loading={flowLoadingKey === `${cacheId}:${flowRange}`}
                                onRetry={handleRetryFlowHistory}
                              />
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}
      </div>
    </section>
  );
}
