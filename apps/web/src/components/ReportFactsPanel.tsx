"use client";

import { Database } from "lucide-react";
type ReportFactsPanelProps = {
  facts: Record<string, unknown> | undefined;
  embedded?: boolean;
};

export function ReportFactsPanel({ facts, embedded = false }: ReportFactsPanelProps) {
  if (!facts || !facts.portfolio) {
    return null;
  }

  const portfolio = facts.portfolio as Record<string, number | string>;
  const holdings = (facts.holdings as Array<Record<string, unknown>>) ?? [];

  const content = (
    <>
      <div className="grid gap-3 sm:grid-cols-3">
        <Fact label="账户总额" value={`¥${portfolio.total_amount}`} />
        <Fact label="加权收益率" value={`${portfolio.weighted_return_percent}%`} />
        <Fact label="风险等级" value={String(portfolio.risk_level)} />
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="min-w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="px-2 py-1">基金</th>
              <th className="px-2 py-1">仓位%</th>
              <th className="px-2 py-1">持有收益%</th>
              <th className="px-2 py-1">板块%</th>
              <th className="px-2 py-1">估算当日%</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((row) => (
              <tr key={String(row.fund_code)} className="border-t border-slate-100 text-slate-700">
                <td className="px-2 py-2 font-semibold">{String(row.fund_name)}</td>
                <td className="px-2 py-2">{String(row.weight_percent)}</td>
                <td className="px-2 py-2">{String(row.holding_return_percent ?? "—")}</td>
                <td className="px-2 py-2">{String(row.sector_return_percent ?? "—")}</td>
                <td className="px-2 py-2">
                  {String(row.estimated_daily_return_percent ?? "—")}
                  {row.daily_return_is_estimated ? "（估）" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );

  if (embedded) {
    return content;
  }

  return (
    <details className="mb-5 rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-black text-slate-950">
        <Database size={18} className="text-indigo-600" />
        系统计算事实（只读，模型不得改写）
      </summary>
      <div className="mt-4">{content}</div>
    </details>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-slate-50 px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-black text-slate-900">{value}</div>
    </div>
  );
}
