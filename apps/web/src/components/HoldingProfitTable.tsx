"use client";

import { memo, useMemo } from "react";
import { cnProfitClass, formatSignedMoney } from "@/lib/holdingMetrics";
import type { HoldingProfitPoint } from "@/lib/holdingProfitTrend";

type HoldingProfitTableProps = {
  points: HoldingProfitPoint[];
  maxRows?: number;
};

function HoldingProfitTableView({ points, maxRows = 120 }: HoldingProfitTableProps) {
  const rows = useMemo(
    () => [...points].sort((left, right) => right.date.localeCompare(left.date)).slice(0, maxRows),
    [maxRows, points],
  );

  if (rows.length === 0) {
    return (
      <div className="px-4 py-6 text-center text-sm text-slate-500">暂无历史收益数据</div>
    );
  }

  return (
    <div className="overflow-hidden">
      <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold text-slate-500">
        <span>日期</span>
        <span className="text-center">日收益</span>
        <span className="text-right">累计收益</span>
      </div>
      <div>
        {rows.map((row) => (
          <div
            key={row.date}
            style={{ contentVisibility: "auto", containIntrinsicSize: "auto 38px" }}
            className="grid grid-cols-3 border-b border-slate-50 px-4 py-2.5 text-[13px] tabular-nums"
          >
            <span className="text-slate-600">{row.date}</span>
            <span className={`text-center font-semibold ${cnProfitClass(row.dailyProfit)}`}>
              {formatSignedMoney(row.dailyProfit)}
            </span>
            <span className={`text-right font-bold ${cnProfitClass(row.cumulativeProfit)}`}>
              {formatSignedMoney(row.cumulativeProfit)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export const HoldingProfitTable = memo(HoldingProfitTableView);
