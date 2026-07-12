"use client";

import { useState } from "react";
import type { DailyProfitTop5Row } from "@/lib/api";

type DailyProfitTop5Props = {
  gainers: DailyProfitTop5Row[];
  losers: DailyProfitTop5Row[];
};

function formatMoney(value: number) {
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}`;
}

function profitClass(value: number) {
  return value > 0 ? "profit-up" : "profit-down";
}

export function DailyProfitTop5({ gainers, losers }: DailyProfitTop5Props) {
  const [tab, setTab] = useState<"gainers" | "losers">("gainers");
  const rows = tab === "gainers" ? gainers : losers;
  const maxAbs = Math.max(...rows.map((row) => Math.abs(row.daily_profit)), 1);

  return (
    <section className="pl-panel">
      <div className="pl-panel-head">
        <div className="pl-panel-title">当日盈亏</div>
        <div className="pl-toggle">
          <button
            type="button"
            aria-pressed={tab === "gainers"}
            className="pl-toggle-btn"
            onClick={() => setTab("gainers")}
          >
            盈利
          </button>
          <button
            type="button"
            aria-pressed={tab === "losers"}
            className="pl-toggle-btn"
            onClick={() => setTab("losers")}
          >
            亏损
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="py-6 text-center text-sm text-slate-500">
          暂无{tab === "gainers" ? "盈利" : "亏损"}基金
        </div>
      ) : (
        <ul className="space-y-2">
          {rows.map((row, index) => (
            <li
              key={`${row.fund_code}-${row.fund_name}`}
              className="flex items-center gap-3 rounded-xl bg-slate-50/80 px-3 py-2.5"
            >
              <span className="w-4 shrink-0 text-center text-xs font-black text-slate-500">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-bold text-slate-800">{row.fund_name}</div>
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-200/80">
                  <div
                    className={`h-full rounded-full ${row.daily_profit > 0 ? "bg-rose-400" : "bg-emerald-400"}`}
                    style={{ width: `${(Math.abs(row.daily_profit) / maxAbs) * 100}%` }}
                  />
                </div>
              </div>
              <div className={`shrink-0 text-sm font-black tabular-nums ${profitClass(row.daily_profit)}`}>
                {formatMoney(row.daily_profit)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
