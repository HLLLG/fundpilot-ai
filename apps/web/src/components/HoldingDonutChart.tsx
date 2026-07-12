"use client";

import { useMemo } from "react";
import type { PortfolioAllocationRow } from "@/lib/api";

const SLICE_COLORS = [
  "#2356e0",
  "#3d7eff",
  "#6b9af5",
  "#0f766e",
  "#d39a21",
  "#64748b",
];

const MAX_VISIBLE_SLICES = 6;

type HoldingDonutChartProps = {
  rows: PortfolioAllocationRow[];
};

function polarToCartesian(cx: number, cy: number, radius: number, angle: number) {
  const radians = ((angle - 90) * Math.PI) / 180;
  return {
    x: cx + radius * Math.cos(radians),
    y: cy + radius * Math.sin(radians),
  };
}

function describeArc(
  cx: number,
  cy: number,
  radius: number,
  startAngle: number,
  endAngle: number,
) {
  const start = polarToCartesian(cx, cy, radius, endAngle);
  const end = polarToCartesian(cx, cy, radius, startAngle);
  const largeArc = endAngle - startAngle <= 180 ? 0 : 1;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 0 ${end.x} ${end.y}`;
}

export function HoldingDonutChart({ rows }: HoldingDonutChartProps) {
  const displayRows = useMemo(() => {
    const sorted = [...rows].sort((left, right) => right.weight_percent - left.weight_percent);
    if (sorted.length <= MAX_VISIBLE_SLICES) {
      return sorted;
    }
    const leaders = sorted.slice(0, MAX_VISIBLE_SLICES - 1);
    const remainder = sorted.slice(MAX_VISIBLE_SLICES - 1);
    return [
      ...leaders,
      {
        fund_code: "__other__",
        fund_name: "其他",
        holding_amount: remainder.reduce((sum, row) => sum + row.holding_amount, 0),
        weight_percent: remainder.reduce((sum, row) => sum + row.weight_percent, 0),
        daily_profit: remainder.some((row) => row.daily_profit != null)
          ? remainder.reduce((sum, row) => sum + (row.daily_profit ?? 0), 0)
          : null,
        holding_return_percent: null,
      },
    ];
  }, [rows]);

  const slices = useMemo(() => {
    const total = displayRows.reduce((sum, row) => sum + row.weight_percent, 0) || 100;
    return displayRows.reduce<
      Array<{ row: PortfolioAllocationRow; start: number; end: number; color: string }>
    >((acc, row, index) => {
      const sweep = (row.weight_percent / total) * 360;
      const start = acc.length ? acc[acc.length - 1].end : 0;
      acc.push({
        row,
        start,
        end: start + sweep,
        color: SLICE_COLORS[index % SLICE_COLORS.length],
      });
      return acc;
    }, []);
  }, [displayRows]);

  if (!rows.length) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
        暂无持仓分布数据
      </div>
    );
  }

  const size = 220;
  const cx = size / 2;
  const cy = size / 2;
  const radius = 86;
  const innerRadius = 54;

  return (
    <div className="grid justify-items-center gap-4 lg:grid-cols-[auto_minmax(0,1fr)] lg:items-start">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="h-56 w-56 shrink-0"
        role="img"
        aria-label={`持仓分布图，共${rows.length}只基金；最大持仓${slices[0]?.row.fund_name ?? "未知"}${slices[0]?.row.weight_percent.toFixed(1) ?? "0"}%`}
      >
        <circle cx={cx} cy={cy} r={innerRadius} fill="#fff" />
        {slices.map((slice) => (
          <path
            key={`${slice.row.fund_code}-${slice.row.fund_name}`}
            d={`${describeArc(cx, cy, radius, slice.start, slice.end)} L ${cx} ${cy} Z`}
            fill={slice.color}
            opacity={0.92}
          >
            <title>{`${slice.row.fund_name} ${slice.row.weight_percent.toFixed(1)}%`}</title>
          </path>
        ))}
        <circle cx={cx} cy={cy} r={innerRadius} fill="#fff" />
        <text x={cx} y={cy - 4} textAnchor="middle" className="fill-slate-900 text-[13px] font-black">
          持仓
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" className="fill-slate-500 text-[11px] font-semibold">
          {rows.length} 只
        </text>
      </svg>
      <div className="w-full max-w-sm">
      <ul className="grid gap-2" aria-label="持仓占比图例">
        {slices.map((slice) => (
          <li key={`${slice.row.fund_code}-legend`} className="flex min-h-8 items-center gap-2 text-sm">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: slice.color }} aria-hidden />
            <span className="min-w-0 flex-1 truncate font-semibold text-slate-800">{slice.row.fund_name}</span>
            <span className="font-black text-slate-900">{slice.row.weight_percent.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
      {rows.length > MAX_VISIBLE_SLICES ? (
        <details className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <summary className="flex min-h-11 cursor-pointer items-center px-3 text-xs font-bold text-slate-700">
            查看全部 {rows.length} 只持仓明细
          </summary>
          <ul className="max-h-64 space-y-2 overflow-y-auto border-t border-slate-100 p-3 text-xs">
            {[...rows]
              .sort((left, right) => right.weight_percent - left.weight_percent)
              .map((row) => (
                <li key={`full-${row.fund_code}-${row.fund_name}`} className="flex items-start justify-between gap-3">
                  <span className="min-w-0 break-words text-slate-700">{row.fund_name}</span>
                  <span className="shrink-0 font-black tabular-nums text-slate-900">{row.weight_percent.toFixed(1)}%</span>
                </li>
              ))}
          </ul>
        </details>
      ) : null}
      </div>
    </div>
  );
}
