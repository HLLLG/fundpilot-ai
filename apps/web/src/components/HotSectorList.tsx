"use client";

import { Loader2, RotateCcw } from "lucide-react";
import type { MarketBoardSort, MarketBoardType, MarketSectorBoardList } from "@/lib/api";
import { isMarketListUsable } from "@/lib/marketSectorBoard";

type HotSectorListProps = {
  data: MarketSectorBoardList | null;
  loading: boolean;
  revalidating: boolean;
  boardType: MarketBoardType;
  sort: MarketBoardSort;
  onBoardTypeChange: (boardType: MarketBoardType) => void;
  onSortChange: (sort: MarketBoardSort) => void;
  onRefresh: () => void;
};

function formatPercent(value: number | null | undefined) {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

function formatInflow(value: number | null | undefined) {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}亿`;
}

function profitClass(value: number | null | undefined) {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

export function HotSectorList({
  data,
  loading,
  revalidating,
  boardType,
  sort,
  onBoardTypeChange,
  onSortChange,
  onRefresh,
}: HotSectorListProps) {
  const valueKey = sort === "change" ? "change_percent" : "main_force_net_yi";
  const formatValue = sort === "change" ? formatPercent : formatInflow;
  const columnLabel = sort === "change" ? "日涨幅" : "主力净流入";

  const showData = isMarketListUsable(data);

  return (
    <section
      id="hot-sector-list"
      className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-slate-900">热门板块</h2>
        <button
          type="button"
          onClick={onRefresh}
          disabled={revalidating}
          className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-slate-100"
        >
          {revalidating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
          刷新
        </button>
      </div>

      <div className="tab-segment mb-3">
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={boardType === "industry"}
          onClick={() => onBoardTypeChange("industry")}
        >
          行业
        </button>
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={boardType === "concept"}
          onClick={() => onBoardTypeChange("concept")}
        >
          概念
        </button>
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onSortChange("change")}
          className={`rounded-full px-3 py-1 text-xs ${
            sort === "change"
              ? "bg-rose-50 text-rose-600"
              : "bg-slate-100 text-slate-600 hover:bg-slate-200"
          }`}
        >
          涨幅领先
        </button>
        <button
          type="button"
          onClick={() => onSortChange("inflow")}
          className={`rounded-full px-3 py-1 text-xs ${
            sort === "inflow"
              ? "bg-sky-50 text-sky-700"
              : "bg-slate-100 text-slate-600 hover:bg-slate-200"
          }`}
        >
          资金流入
        </button>
      </div>

      <div className="mb-2 flex items-center justify-between px-1 text-xs text-slate-500">
        <span>板块名称</span>
        <span>{columnLabel}</span>
      </div>

      {loading ? (
        <p className="py-8 text-center text-sm text-slate-500">加载列表…</p>
      ) : !showData ? (
        <p className="py-8 text-center text-sm text-slate-500">
          {data?.message ?? "板块列表暂不可用"}
        </p>
      ) : (
        <ul className="divide-y divide-[var(--line)]">
          {data!.items.map((item) => (
            <li key={item.rank} className="flex items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-slate-900">{item.name}</div>
                {item.code ? <div className="text-xs text-slate-400">{item.code}</div> : null}
              </div>
              <div className={`shrink-0 text-sm font-semibold ${profitClass(item[valueKey])}`}>
                {formatValue(item[valueKey])}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
