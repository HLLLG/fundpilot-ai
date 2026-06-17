"use client";

import { Loader2, RotateCcw } from "lucide-react";
import type { MarketThemeBoardResponse, MarketThemeBoardSort } from "@/lib/api";
import { isMarketThemeBoardUsable } from "@/lib/marketThemeBoard";

type ThemeSectorOverviewProps = {
  data: MarketThemeBoardResponse | null;
  loading: boolean;
  revalidating: boolean;
  sort: MarketThemeBoardSort;
  onSortChange: (sort: MarketThemeBoardSort) => void;
  onRefresh: () => void;
};

function formatPercent(value: number | null | undefined) {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

function profitClass(value: number | null | undefined) {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

export function ThemeSectorOverview({
  data,
  loading,
  revalidating,
  sort,
  onSortChange,
  onRefresh,
}: ThemeSectorOverviewProps) {
  const showData = isMarketThemeBoardUsable(data);

  return (
    <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-slate-900">主题板块</h2>
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
          aria-pressed={sort === "change"}
          onClick={() => onSortChange("change")}
        >
          涨幅领先
        </button>
        <button
          type="button"
          className="tab-segment-btn"
          aria-pressed={sort === "streak"}
          onClick={() => onSortChange("streak")}
        >
          连涨天数
        </button>
      </div>

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
              <tr className="border-b border-[var(--line)] text-left text-xs text-slate-500">
                <th className="pb-2 font-medium">板块名称</th>
                <th className="pb-2 text-right font-medium">日涨幅</th>
                <th className="pb-2 text-right font-medium">连涨天数</th>
                <th className="pb-2 text-right font-medium">我的持仓</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((item) => (
                <tr
                  key={item.sector_label}
                  className={`border-b border-[var(--line)] last:border-0 ${
                    item.in_portfolio ? "bg-sky-50/60" : ""
                  }`}
                >
                  <td className="py-2.5 pr-2">
                    <div className="font-medium text-slate-900">{item.sector_label}</div>
                    <div className="text-xs text-slate-400">{item.linked_fund_count}只基金</div>
                  </td>
                  <td className={`py-2.5 text-right tabular-nums ${profitClass(item.change_1d_percent)}`}>
                    {formatPercent(item.change_1d_percent)}
                  </td>
                  <td className={`py-2.5 text-right tabular-nums ${profitClass(item.consecutive_up_days)}`}>
                    {item.consecutive_up_days == null ? "—" : item.consecutive_up_days}
                  </td>
                  <td className="py-2.5 text-right tabular-nums text-slate-700">
                    {item.held_fund_count > 0 ? `${item.held_fund_count}只` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
