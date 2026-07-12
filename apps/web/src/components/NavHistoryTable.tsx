"use client";

import type { PerformanceSeriesPoint } from "@/lib/performanceTrend";
import { formatSignedPercent } from "@/lib/performanceTrend";

type NavHistoryTableProps = {
  points: PerformanceSeriesPoint[];
  maxRows?: number;
};

function cnDailyReturn(value: number | null) {
  if (value == null || Math.abs(value) < 0.005) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

export function NavHistoryTable({ points, maxRows = 120 }: NavHistoryTableProps) {
  const rows = [...points].sort((left, right) => right.date.localeCompare(left.date)).slice(0, maxRows);

  if (rows.length === 0) {
    return (
      <div className="px-4 py-6 text-center text-sm text-slate-500">暂无历史净值数据</div>
    );
  }

  return (
    <div className="overflow-hidden">
      <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold text-slate-500">
        <span>日期</span>
        <span className="text-center">净值</span>
        <span className="text-right">日涨幅</span>
      </div>
      <div>
        {rows.map((row) => (
          <div
            key={row.date}
            className="grid grid-cols-3 border-b border-slate-50 px-4 py-2.5 text-[13px] tabular-nums"
          >
            <span className="text-slate-600">{row.date}</span>
            <span className="text-center font-semibold text-slate-900">{row.nav.toFixed(4)}</span>
            <span className={`text-right font-bold ${cnDailyReturn(row.dailyReturn)}`}>
              {formatSignedPercent(row.dailyReturn)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
