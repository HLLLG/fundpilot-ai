"use client";

import { ChevronRight, Loader2 } from "lucide-react";
import type { MarketSectorBoardWidget, SectorBoardItem } from "@/lib/api";
import { isMarketWidgetUsable } from "@/lib/marketSectorBoard";

type SectorMetric = "change" | "inflow";

type SectorPerformanceCardProps = {
  data: MarketSectorBoardWidget | null;
  loading: boolean;
  revalidating?: boolean;
  metric: SectorMetric;
  onMetricChange: (metric: SectorMetric) => void;
  onOpenDetail: () => void;
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

function tileTone(value: number | null | undefined, metric: SectorMetric) {
  if (value == null || value === 0) {
    return "bg-slate-100";
  }
  if (metric === "change") {
    return value > 0 ? "bg-rose-50" : "bg-emerald-50";
  }
  return value > 0 ? "bg-rose-50" : "bg-emerald-50";
}

function renderTiles(
  positive: SectorBoardItem[],
  negative: SectorBoardItem[],
  metric: SectorMetric,
) {
  const formatValue = metric === "change" ? formatPercent : formatInflow;
  const valueKey = metric === "change" ? "change_percent" : "main_force_net_yi";

  return (
    <div className="grid grid-cols-3 gap-2">
      {positive.map((item) => (
        <div
          key={`pos-${item.name}`}
          className={`rounded-xl px-2 py-3 text-center ${tileTone(item[valueKey], metric)}`}
        >
          <div className="truncate text-xs text-slate-600">{item.name}</div>
          <div className={`mt-1 text-sm font-semibold ${profitClass(item[valueKey])}`}>
            {formatValue(item[valueKey])}
          </div>
        </div>
      ))}
      {negative.map((item) => (
        <div
          key={`neg-${item.name}`}
          className={`rounded-xl px-2 py-3 text-center ${tileTone(item[valueKey], metric)}`}
        >
          <div className="truncate text-xs text-slate-600">{item.name}</div>
          <div className={`mt-1 text-sm font-semibold ${profitClass(item[valueKey])}`}>
            {formatValue(item[valueKey])}
          </div>
        </div>
      ))}
    </div>
  );
}

export function SectorPerformanceCard({
  data,
  loading,
  revalidating = false,
  metric,
  onMetricChange,
  onOpenDetail,
}: SectorPerformanceCardProps) {
  const showData = isMarketWidgetUsable(data);
  const positive =
    metric === "change" ? (data?.top_gainers ?? []) : (data?.top_inflow ?? []);
  const negative =
    metric === "change" ? (data?.top_losers ?? []) : (data?.top_outflow ?? []);

  return (
    <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
      <button
        type="button"
        onClick={onOpenDetail}
        className="mb-3 flex w-full items-center justify-between text-left"
      >
        <h2 className="text-base font-semibold text-slate-900">板块表现</h2>
        <span className="inline-flex items-center gap-1 text-slate-400">
          {revalidating ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
          <ChevronRight className="h-4 w-4" aria-hidden />
        </span>
      </button>

      <div className="tab-segment mb-3">
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={metric === "change"}
          onClick={() => onMetricChange("change")}
        >
          涨跌幅
        </button>
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={metric === "inflow"}
          onClick={() => onMetricChange("inflow")}
        >
          主力净流入
        </button>
      </div>

      {loading ? (
        <p className="py-8 text-center text-sm text-slate-500">加载板块行情…</p>
      ) : !showData ? (
        <p className="py-8 text-center text-sm text-slate-500">
          {data?.message ?? "板块行情暂不可用"}
        </p>
      ) : (
        renderTiles(positive, negative, metric)
      )}
    </section>
  );
}
