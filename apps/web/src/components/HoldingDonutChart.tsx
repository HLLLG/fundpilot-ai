"use client";

import { useMemo } from "react";
import type { PortfolioAllocationRow } from "@/lib/api";

const SLICE_COLORS = [
  "#3b82f6",
  "#6366f1",
  "#8b5cf6",
  "#ec4899",
  "#f97316",
  "#14b8a6",
  "#22c55e",
  "#eab308",
];

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
  const slices = useMemo(() => {
    const total = rows.reduce((sum, row) => sum + row.weight_percent, 0) || 100;
    return rows.reduce<
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
  }, [rows]);

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
    <div className="flex flex-col items-center gap-4 lg:flex-row lg:items-start lg:justify-center">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-56 w-56 shrink-0">
        <circle cx={cx} cy={cy} r={innerRadius} fill="#fff" />
        {slices.map((slice) => (
          <path
            key={`${slice.row.fund_code}-${slice.row.fund_name}`}
            d={`${describeArc(cx, cy, radius, slice.start, slice.end)} L ${cx} ${cy} Z`}
            fill={slice.color}
            opacity={0.92}
          />
        ))}
        <circle cx={cx} cy={cy} r={innerRadius} fill="#fff" />
        <text x={cx} y={cy - 4} textAnchor="middle" className="fill-slate-900 text-[13px] font-black">
          持仓
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" className="fill-slate-500 text-[11px] font-semibold">
          {rows.length} 只
        </text>
      </svg>
      <div className="grid w-full max-w-sm gap-2">
        {slices.map((slice) => (
          <div key={`${slice.row.fund_code}-legend`} className="flex items-center gap-2 text-sm">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: slice.color }} />
            <span className="min-w-0 flex-1 truncate font-semibold text-slate-800">{slice.row.fund_name}</span>
            <span className="font-black text-slate-900">{slice.row.weight_percent.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
