"use client";

import { Loader2, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import type { MarketThemeBoardResponse } from "@/lib/api";
import {
  formatConsecutiveDays,
  formatThemeBoardUpdatedAt,
  formatThemePercent,
  formatThemeRank,
  isMarketThemeBoardUsable,
  profitToneClass,
  themeBoardHeading,
  themeRankClass,
} from "@/lib/marketThemeBoard";

type ThemeSectorOverviewProps = {
  data: MarketThemeBoardResponse | null;
  loading: boolean;
  revalidating: boolean;
  onRefresh: () => void;
};

export function ThemeSectorOverview({
  data,
  loading,
  revalidating,
  onRefresh,
}: ThemeSectorOverviewProps) {
  const showData = isMarketThemeBoardUsable(data);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    if (showData && !revalidating) {
      setUpdatedAt(new Date());
    }
  }, [data, showData, revalidating]);

  return (
    <section className="overflow-hidden rounded-2xl border border-[var(--line)] bg-[var(--panel)] shadow-sm">
      <div className="bg-gradient-to-b from-amber-50/90 via-amber-50/30 to-white px-4 pb-3 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-lg font-bold tracking-tight text-slate-900 sm:text-xl">{themeBoardHeading()}</h2>
            <p className="mt-1 text-xs text-slate-400">
              {updatedAt ? formatThemeBoardUpdatedAt(updatedAt) : "加载中…"}
              {data?.stale ? " · 行情暂不可用" : ""}
            </p>
            {data?.message?.includes("连涨") ? (
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
            <table className="w-full min-w-[300px] text-sm">
              <thead>
                <tr className="text-xs text-slate-400">
                  <th className="pb-2 pr-2 text-left font-medium">排名</th>
                  <th className="pb-2 pr-2 text-left font-medium">板块名称</th>
                  <th className="pb-2 text-right font-medium">连涨/连跌天数</th>
                  <th className="pb-2 text-right font-medium">涨跌幅</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((item, index) => (
                  <tr
                    key={item.sector_label}
                    className={`border-t border-[var(--line)] first:border-t-0 ${
                      item.in_portfolio ? "bg-sky-50/50" : ""
                    }`}
                  >
                    <td className={`py-3 pr-2 tabular-nums ${themeRankClass(item.rank, index)}`}>
                      {formatThemeRank(item.rank, index)}
                    </td>
                    <td className="py-3 pr-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-medium text-slate-900">{item.sector_label}</span>
                        {item.in_portfolio ? (
                          <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700">
                            持仓
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td className={`py-3 text-right tabular-nums ${profitToneClass(item.consecutive_up_days)}`}>
                      {formatConsecutiveDays(item.consecutive_up_days)}
                    </td>
                    <td className={`py-3 text-right tabular-nums font-medium ${profitToneClass(item.change_1d_percent)}`}>
                      {formatThemePercent(item.change_1d_percent)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
