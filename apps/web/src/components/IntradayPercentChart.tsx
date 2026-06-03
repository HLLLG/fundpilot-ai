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

export function IntradayPercentChart({ points, height = 200 }: IntradayPercentChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const chart = useMemo(() => {
    if (points.length < 2) {
      return null;
    }
    const values = points.map((point) => point.percent);
    const min = Math.min(...values, 0);
    const max = Math.max(...values, 0);
    const padding = { top: 16, right: 12, bottom: 28, left: 44 };
    const width = 360;
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const range = max - min || 1;

    const coords = points.map((point, index) => {
      const x = padding.left + (index / (points.length - 1)) * chartWidth;
      const y = padding.top + chartHeight - ((point.percent - min) / range) * chartHeight;
      return { ...point, x, y, index };
    });

    const linePath = coords.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
    const areaPath = `${linePath} L ${coords[coords.length - 1].x} ${padding.top + chartHeight} L ${coords[0].x} ${padding.top + chartHeight} Z`;
    const baselineY = padding.top + chartHeight - ((0 - min) / range) * chartHeight;
    const latest = coords[coords.length - 1];
    const trend = latest.percent >= 0 ? "up" : "down";
    const colors =
      trend === "up"
        ? { line: "#e11d48", fillStart: "rgba(225,29,72,0.2)", fillEnd: "rgba(225,29,72,0.02)" }
        : { line: "#059669", fillStart: "rgba(5,150,105,0.18)", fillEnd: "rgba(5,150,105,0.02)" };

    const yTicks = [min, min / 2, 0, max / 2, max].filter(
      (value, index, array) => array.indexOf(value) === index,
    );

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
      min,
      max,
      yTicks,
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
        {chart.yTicks.map((tick) => {
          const y =
            chart.padding.top +
            chart.chartHeight -
            ((tick - chart.min) / (chart.max - chart.min || 1)) * chart.chartHeight;
          return (
            <g key={tick}>
              <line
                x1={chart.padding.left}
                y1={y}
                x2={chart.width - chart.padding.right}
                y2={y}
                stroke="#e2e8f0"
                strokeWidth={1}
              />
              <text
                x={chart.padding.left - 6}
                y={y + 4}
                textAnchor="end"
                className="fill-slate-400 text-[10px]"
              >
                {tick > 0 ? "+" : ""}
                {tick.toFixed(2)}%
              </text>
            </g>
          );
        })}
        <line
          x1={chart.padding.left}
          y1={chart.baselineY}
          x2={chart.width - chart.padding.right}
          y2={chart.baselineY}
          stroke="#94a3b8"
          strokeDasharray="4 4"
        />
        <path d={chart.areaPath} fill={`url(#${gradientId})`} />
        <path d={chart.linePath} fill="none" stroke={chart.colors.line} strokeWidth={2} />
        <circle cx={active.x} cy={active.y} r={4} fill={chart.colors.line} />
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
      <div className="absolute left-3 top-2 rounded-lg bg-white/90 px-2 py-1 text-[11px] font-bold tabular-nums text-slate-700 shadow-sm">
        {formatTimeLabel(active.time)} · {active.percent > 0 ? "+" : ""}
        {active.percent.toFixed(2)}%
      </div>
    </div>
  );
}
