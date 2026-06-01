"use client";

import { useEffect, useState } from "react";
import { BarChart3, LineChart, PieChart } from "lucide-react";
import type { PortfolioDashboardData } from "@/lib/api";
import { fetchPortfolioDashboard } from "@/lib/api";
import { PortfolioSummaryCard } from "@/components/PortfolioSummaryCard";

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `¥${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function profitClass(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "text-rose-600" : "text-emerald-600";
}

type SparklineProps = {
  points: Array<{ label: string; value: number | null | undefined }>;
  strokeClass?: string;
  formatValue?: (value: number) => string;
};

function Sparkline({ points, strokeClass = "stroke-blue-600", formatValue }: SparklineProps) {
  const values = points.map((point) => point.value).filter((v): v is number => v != null);
  if (values.length < 2) {
    return (
      <div className="flex h-36 items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
        上传总览截图后会自动记录，满 2 天即可看到走势
      </div>
    );
  }

  const width = 320;
  const height = 120;
  const padding = 12;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const coords = values.map((value, index) => {
    const x = padding + (index / (values.length - 1)) * (width - padding * 2);
    const y = height - padding - ((value - min) / range) * (height - padding * 2);
    return `${x},${y}`;
  });

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-36 w-full">
        <polyline
          fill="none"
          className={strokeClass}
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={coords.join(" ")}
        />
        {values.map((value, index) => {
          const [x, y] = coords[index].split(",").map(Number);
          return <circle key={index} cx={x} cy={y} r="3.5" className="fill-white stroke-blue-600" strokeWidth="2" />;
        })}
      </svg>
      <div className="mt-2 flex justify-between text-[10px] font-semibold text-slate-400">
        <span>{points[0]?.label}</span>
        <span>{points[points.length - 1]?.label}</span>
      </div>
      {formatValue ? (
        <div className="mt-1 text-xs text-slate-500">
          最新 {formatValue(values[values.length - 1])}
        </div>
      ) : null}
    </div>
  );
}

export function PortfolioDashboard() {
  const [data, setData] = useState<PortfolioDashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setData(await fetchPortfolioDashboard());
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载仪表盘失败");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const summary = data?.summary ?? null;
  const history = data?.history ?? [];
  const allocation = data?.allocation ?? [];

  return (
    <div className="grid gap-6">
      <PortfolioSummaryCard summary={summary} />

      {error ? (
        <div className="rounded-2xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="glass-panel rounded-[24px] p-5">
          <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
            <LineChart size={18} className="text-blue-600" />
            账户资产走势
          </div>
          <Sparkline
            points={history.map((row) => ({
              label: row.date.slice(5),
              value: row.total_assets,
            }))}
            formatValue={(value) => formatMoney(value)}
          />
        </section>

        <section className="glass-panel rounded-[24px] p-5">
          <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
            <BarChart3 size={18} className="text-indigo-600" />
            当日收益走势
          </div>
          <Sparkline
            points={history.map((row) => ({
              label: row.date.slice(5),
              value: row.daily_profit,
            }))}
            strokeClass="stroke-indigo-600"
            formatValue={(value) => formatMoney(value)}
          />
        </section>
      </div>

      <section className="glass-panel rounded-[24px] p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-black text-slate-950">
            <PieChart size={18} className="text-violet-600" />
            持仓分布
          </div>
          {data?.latest_snapshot_date ? (
            <span className="text-xs text-slate-400">数据日 {data.latest_snapshot_date}</span>
          ) : null}
        </div>

        {allocation.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
            暂无分布数据。在「今日」上传养基宝总览后会自动记录。
          </div>
        ) : (
          <div className="space-y-3">
            {allocation.map((row) => (
              <div key={`${row.fund_code}-${row.fund_name}`} className="rounded-2xl bg-white px-4 py-3 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-bold text-slate-900">{row.fund_name}</div>
                    <div className="text-xs text-slate-400">{row.fund_code}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-black text-slate-950">{formatMoney(row.holding_amount)}</div>
                    <div className="text-xs font-bold text-slate-500">{row.weight_percent.toFixed(1)}%</div>
                  </div>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-500"
                    style={{ width: `${Math.min(row.weight_percent, 100)}%` }}
                  />
                </div>
                <div className="mt-2 flex justify-between text-xs">
                  <span className={profitClass(row.daily_profit)}>
                    当日 {row.daily_profit != null ? formatMoney(row.daily_profit) : "—"}
                  </span>
                  <span className={profitClass(row.holding_return_percent)}>
                    持有{" "}
                    {row.holding_return_percent != null
                      ? `${row.holding_return_percent > 0 ? "+" : ""}${row.holding_return_percent.toFixed(2)}%`
                      : "—"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <button
        type="button"
        onClick={() => void load()}
        className="justify-self-start rounded-full border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-600 transition hover:border-blue-300 hover:text-blue-700"
      >
        刷新仪表盘
      </button>
    </div>
  );
}
