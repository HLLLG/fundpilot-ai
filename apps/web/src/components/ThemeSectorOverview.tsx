"use client";

import { Fragment, useMemo, useState, type MouseEvent } from "react";
import { ChevronDown, ChevronRight, ChevronUp, Loader2, RotateCcw } from "lucide-react";
import type { MarketThemeBoardItem, MarketThemeBoardResponse } from "@/lib/api";
import {
  boardKindClass,
  formatBoardKindLabel,
  formatThemeBoardUpdatedFromIso,
  formatThemeFlowYi,
  formatThemePercent,
  formatThemeRank,
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
  revalidating: boolean;
  onRefresh: () => void;
  onViewDipFunds?: (sectorLabel: string) => void;
  onAddFocusSector?: (sectorLabel: string) => void;
  focusSectors?: string[];
};

function FlowTierGrid({ item }: { item: MarketThemeBoardItem }) {
  const tiers = item.flow_tiers;
  if (!tiers) {
    return null;
  }
  return (
    <div className="mt-2 space-y-2 border-t border-slate-100 pt-2">
      <div className="grid grid-cols-2 gap-2 text-xs">
        {THEME_FLOW_TIER_ROWS.map(({ key, label, hint }) => {
          const value = tiers[key];
          return (
            <div key={key} className="rounded-lg bg-white/80 px-2.5 py-2">
              <div className="text-slate-400">
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
      <p className="text-[10px] text-slate-400">主力净流入 = 超大单 + 大单；涨幅与资金可能来自不同口径（指数 vs 东财板块）。</p>
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
      className={`inline-flex w-full items-center justify-end gap-0.5 font-medium transition-colors ${
        active ? "text-slate-700" : "text-slate-400 hover:text-slate-600"
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

export function ThemeSectorOverview({
  data,
  loading,
  revalidating,
  onRefresh,
  onViewDipFunds,
  onAddFocusSector,
  focusSectors = [],
}: ThemeSectorOverviewProps) {
  const [expandedLabel, setExpandedLabel] = useState<string | null>(null);
  const [sortColumn, setSortColumn] = useState<ThemeSortColumn>("change");
  const [sortDirection, setSortDirection] = useState<ThemeSortDirection>("desc");
  const showData = isMarketThemeBoardUsable(data);

  const sortedItems = useMemo(
    () => sortThemeBoardItems(data?.items ?? [], sortColumn, sortDirection),
    [data?.items, sortColumn, sortDirection],
  );

  const handleSort = (column: ThemeSortColumn) => {
    const next = nextThemeSortState(column, sortColumn, sortDirection);
    setSortColumn(next.column);
    setSortDirection(next.direction);
  };

  const toggleExpand = (item: MarketThemeBoardItem) => {
    if (!hasThemeFlowDetail(item)) {
      return;
    }
    setExpandedLabel((current) => (current === item.sector_label ? null : item.sector_label));
  };

  const handleRowAction = (
    event: MouseEvent,
    action: "dip" | "focus",
    sectorLabel: string,
  ) => {
    event.stopPropagation();
    if (action === "dip") {
      onViewDipFunds?.(sectorLabel);
    } else {
      onAddFocusSector?.(sectorLabel);
    }
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-[var(--line)] bg-[var(--panel)] shadow-sm">
      <div className="bg-gradient-to-b from-amber-50/90 via-amber-50/30 to-white px-4 pb-3 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-lg font-bold tracking-tight text-slate-900 sm:text-xl">{themeBoardHeading()}</h2>
            <p className="mt-1 text-xs text-slate-400">
              {formatThemeBoardUpdatedFromIso(data?.refreshed_at)}
              {data?.stale ? " · 行情暂不可用" : ""}
            </p>
            {data?.message ? (
              <p className="mt-1 text-xs text-amber-700">{data.message}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={revalidating}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-white/80"
            aria-label="刷新主题板块"
          >
            {revalidating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            刷新
          </button>
        </div>
      </div>

      <div className="px-4 pb-4 pt-1">
        {loading && !showData ? (
          <div className="flex items-center justify-center py-10 text-sm text-slate-500">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            加载主题板块…
          </div>
        ) : !showData ? (
          <p className="py-8 text-center text-sm text-slate-500">{data?.message ?? "暂无主题板块数据"}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[320px] text-sm">
              <thead>
                <tr className="text-xs">
                  <th className="w-8 pb-2" aria-hidden />
                  <th className="pb-2 pr-2 text-left font-medium text-slate-400">排名</th>
                  <th className="pb-2 pr-2 text-left font-medium text-slate-400">板块名称</th>
                  <th className="pb-2 pr-2 text-right">
                    <SortColumnHeader
                      label="涨跌幅"
                      column="change"
                      activeColumn={sortColumn}
                      direction={sortDirection}
                      onSort={handleSort}
                    />
                  </th>
                  <th className="pb-2 text-right">
                    <SortColumnHeader
                      label="主力净流入"
                      column="inflow"
                      activeColumn={sortColumn}
                      direction={sortDirection}
                      onSort={handleSort}
                    />
                  </th>
                  <th className="pb-2 text-right font-medium text-slate-400">操作</th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((item, index) => {
                  const expandable = hasThemeFlowDetail(item);
                  const expanded = expandedLabel === item.sector_label;
                  return (
                    <Fragment key={item.sector_label}>
                      <tr
                        className={`border-t border-[var(--line)] first:border-t-0 ${
                          item.in_portfolio ? "bg-sky-50/50" : ""
                        } ${expandable ? "cursor-pointer hover:bg-slate-50/80" : ""}`}
                        onClick={() => toggleExpand(item)}
                      >
                        <td className="py-3 pl-1 text-slate-400">
                          {expandable ? (
                            expanded ? (
                              <ChevronDown className="h-4 w-4" aria-hidden />
                            ) : (
                              <ChevronRight className="h-4 w-4" aria-hidden />
                            )
                          ) : null}
                        </td>
                        <td className={`py-3 pr-2 tabular-nums ${themeRankClass(item.rank, index)}`}>
                          {formatThemeRank(item.rank, index)}
                        </td>
                        <td className="py-3 pr-2">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="font-medium text-slate-900">{item.sector_label}</span>
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${boardKindClass(item.board_kind)}`}
                            >
                              {formatBoardKindLabel(item.board_kind)}
                            </span>
                            {item.in_portfolio ? (
                              <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700">
                                持仓
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td
                          className={`py-3 pr-2 text-right tabular-nums font-medium ${profitToneClass(item.change_1d_percent)}`}
                        >
                          {formatThemePercent(item.change_1d_percent)}
                        </td>
                        <td
                          className={`py-3 pr-2 text-right tabular-nums font-medium ${profitToneClass(item.main_force_net_yi)}`}
                        >
                          {formatThemeFlowYi(item.main_force_net_yi)}
                        </td>
                        <td className="py-3 text-right">
                          <div className="flex flex-col items-end gap-1 sm:flex-row sm:justify-end">
                            <button
                              type="button"
                              className="rounded-md px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
                              onClick={(event) => handleRowAction(event, "dip", item.sector_label)}
                            >
                              看大跌基金
                            </button>
                            <button
                              type="button"
                              className={`rounded-md px-2 py-1 text-[11px] font-medium ${
                                focusSectors.includes(item.sector_label)
                                  ? "bg-[var(--brand-soft)] text-[var(--brand-strong)]"
                                  : "text-[var(--brand-strong)] hover:bg-[var(--brand-soft)]"
                              }`}
                              onClick={(event) => handleRowAction(event, "focus", item.sector_label)}
                            >
                              {focusSectors.includes(item.sector_label) ? "已关注" : "加入关注方向"}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {expanded && item.flow_tiers ? (
                        <tr className="bg-slate-50/60">
                          <td colSpan={6} className="px-3 pb-3 pt-0">
                            <FlowTierGrid item={item} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
