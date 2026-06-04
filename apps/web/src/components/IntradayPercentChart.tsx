"use client";

import { useId, useMemo, useRef, useState } from "react";

export type IntradayPoint = {
  time: string;
  percent: number;
};

type IntradayPercentChartProps = {
  points: IntradayPoint[];
  height?: number;
};

function formatTimeLabel(time: string) {
  if (time.length >= 5) {
    return time.slice(0, 5);
  }
  return time;
}

function formatRangePercent(value: number) {
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

/** 养基宝同款 Y 轴：max(|最高|, |最低|)，以 0 为中心对称 [-span, +span]。 */
function computeSymmetricSpan(points: IntradayPoint[]): number {
  const values = points.map((point) => point.percent);
  if (!values.length) {
    return 0.15;
  }
  const peak = Math.max(Math.abs(Math.max(...values)), Math.abs(Math.min(...values)));
  return Math.max(peak, 0.08);
}

export function IntradayPercentChart({ points, height = 200 }: IntradayPercentChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const chart = useMemo(() => {
    if (points.length < 2) {
      return null;
    }
    const halfSpan = computeSymmetricSpan(points);
    const min = -halfSpan;
    const max = halfSpan;
    const padding = { top: 14, right: 10, bottom: 26, left: 8 };
    const width = 360;
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const range = max - min || 1;
    const plotTop = padding.top;
    const plotBottom = padding.top + chartHeight;

    const coords = points.map((point, index) => {
      const x = padding.left + (index / (points.length - 1)) * chartWidth;
      const rawY = plotBottom - ((point.percent - min) / range) * chartHeight;
      const y = Math.max(plotTop, Math.min(plotBottom, rawY));
      return { ...point, x, y, index };
    });

    const linePath = coords.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
    const baselineY = padding.top + chartHeight / 2;
    const areaPath = `${linePath} L ${coords[coords.length - 1].x} ${baselineY} L ${coords[0].x} ${baselineY} Z`;
    const latest = coords[coords.length - 1];
    const trend = latest.percent >= 0 ? "up" : "down";
    const colors =
      trend === "up"
        ? { line: "#e11d48", fillStart: "rgba(225,29,72,0.22)", fillEnd: "rgba(225,29,72,0.03)" }
        : { line: "#059669", fillStart: "rgba(5,150,105,0.2)", fillEnd: "rgba(5,150,105,0.03)" };

    return {
      width,
      height,
      padding,
      chartWidth,
      chartHeight,
      coords,
      linePath,
      areaPath,
      baselineY,
      halfSpan,
      colors,
      latest,
    };
  }, [height, points]);

  if (!chart) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-400"
        style={{ height }}
      >
        暂无分时数据
      </div>
    );
  }

  const activeIndex = hoverIndex ?? chart.coords.length - 1;
  const active = chart.coords[activeIndex];

  return (
    <div ref={containerRef} className="relative w-full">
      <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="w-full" role="img">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={chart.colors.fillStart} />
            <stop offset="100%" stopColor={chart.colors.fillEnd} />
          </linearGradient>
        </defs>
        <line
          x1={chart.padding.left}
          y1={chart.baselineY}
          x2={chart.width - chart.padding.right}
          y2={chart.baselineY}
          stroke="#cbd5e1"
          strokeWidth={1}
        />
        <path d={chart.areaPath} fill={`url(#${gradientId})`} />
        <path d={chart.linePath} fill="none" stroke={chart.colors.line} strokeWidth={1.25} />
        <circle cx={active.x} cy={active.y} r={3} fill={chart.colors.line} />
        <text
          x={chart.width - chart.padding.right}
          y={chart.padding.top + 4}
          textAnchor="end"
          className="fill-slate-400 text-[10px] font-semibold tabular-nums"
        >
          {formatRangePercent(chart.halfSpan)}
        </text>
        <text
          x={chart.width - chart.padding.right}
          y={chart.padding.top + chart.chartHeight - 2}
          textAnchor="end"
          className="fill-slate-400 text-[10px] font-semibold tabular-nums"
        >
          {formatRangePercent(-chart.halfSpan)}
        </text>
        <text x={chart.padding.left} y={chart.height - 8} className="fill-slate-400 text-[10px]">
          09:30
        </text>
        <text
          x={chart.padding.left + chart.chartWidth / 2}
          y={chart.height - 8}
          textAnchor="middle"
          className="fill-slate-400 text-[10px]"
        >
          11:30/13:00
        </text>
        <text
          x={chart.width - chart.padding.right}
          y={chart.height - 8}
          textAnchor="end"
          className="fill-slate-400 text-[10px]"
        >
          15:00
        </text>
        <rect
          x={chart.padding.left}
          y={chart.padding.top}
          width={chart.chartWidth}
          height={chart.chartHeight}
          fill="transparent"
          onMouseMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const ratio = (event.clientX - rect.left) / rect.width;
            const index = Math.round(ratio * (chart.coords.length - 1));
            setHoverIndex(Math.max(0, Math.min(chart.coords.length - 1, index)));
          }}
          onMouseLeave={() => setHoverIndex(null)}
        />
      </svg>
      <div className="absolute left-2 top-2 rounded-lg bg-white/90 px-2 py-1 text-[11px] font-bold tabular-nums text-slate-700 shadow-sm">
        {formatTimeLabel(active.time)} · {formatRangePercent(active.percent)}
      </div>
    </div>
  );
}
