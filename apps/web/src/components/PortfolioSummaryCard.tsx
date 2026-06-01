"use client";

import { TrendingDown, TrendingUp, Wallet } from "lucide-react";
import type { PortfolioSummary } from "@/lib/api";

type PortfolioSummaryCardProps = {
  summary: PortfolioSummary | null;
};

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `¥${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function profitTone(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) {
    return "text-slate-600";
  }
  return value > 0 ? "text-rose-600" : "text-emerald-600";
}

export function PortfolioSummaryCard({ summary }: PortfolioSummaryCardProps) {
  const dailyProfit = summary?.daily_profit;
  const dailyReturn = summary?.daily_return_percent;
  const updatedAt = summary?.updated_at
    ? new Date(summary.updated_at).toLocaleString("zh-CN")
    : null;

  return (
    <section className="rounded-[24px] border border-indigo-100 bg-gradient-to-br from-indigo-50 via-white to-white p-5 shadow-sm">
      <div className="mb-4 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-indigo-600">
        <Wallet size={16} />
        我的持仓总览
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <div className="text-xs font-semibold text-slate-500">账户资产</div>
          <div className="mt-1 text-2xl font-black text-slate-950">{formatMoney(summary?.total_assets)}</div>
        </div>
        <div>
          <div className="text-xs font-semibold text-slate-500">当日收益</div>
          <div className={`mt-1 flex items-center gap-1 text-2xl font-black ${profitTone(dailyProfit)}`}>
            {dailyProfit !== null && dailyProfit !== undefined && dailyProfit > 0 ? (
              <TrendingUp size={20} />
            ) : dailyProfit !== null && dailyProfit !== undefined && dailyProfit < 0 ? (
              <TrendingDown size={20} />
            ) : null}
            {formatMoney(dailyProfit)}
          </div>
          {dailyReturn !== null && dailyReturn !== undefined ? (
            <div className={`text-xs font-bold ${profitTone(dailyReturn)}`}>
              {dailyReturn > 0 ? "+" : ""}
              {dailyReturn.toFixed(2)}%
            </div>
          ) : null}
        </div>
        <div>
          <div className="text-xs font-semibold text-slate-500">持仓基金</div>
          <div className="mt-1 text-2xl font-black text-slate-950">{summary?.holding_count ?? 0} 只</div>
          {updatedAt ? <div className="mt-1 text-xs text-slate-400">更新于 {updatedAt}</div> : null}
        </div>
      </div>
    </section>
  );
}
