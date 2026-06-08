"use client";

import { useId, useMemo, useRef, useState } from "react";
import type { PerformanceSeriesPoint } from "@/lib/performanceTrend";
import { formatSignedPercent } from "@/lib/performanceTrend";

const Y_AXIS_HEADROOM_RATIO = 0.12;
const FUND_COLOR = "#3d7eff";
const BENCH_COLOR = "#f59e0b";

type PerformanceReturnChartProps = {
  points: PerformanceSeriesPoint[];
  height?: number;
  showBenchmark?: boolean;
};

export function PerformanceReturnChart({
  points,
  height = 220,
  showBenchmark = true,
}: PerformanceReturnChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const chart = useMemo(() => {
    if (points.length < 2) {
      return null;
    }

    const values = points.flatMap((point) =>
      [point.fundPercent, point.benchPercent].filter((value): value is number => value != null),
    );
    const rawMin = Math.min(...values, 0);
    const rawMax = Math.max(...values, 0);
    const span = rawMax - rawMin || 1;
    const pad = span * Y_AXIS_HEADROOM_RATIO;
    const min = rawMin - pad;
    const max = rawMax + pad;
    const range = max - min || 1;

    const padding = { top: 14, right: 10, bottom: 26, left: 8 };
    const width = 360;
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const plotTop = padding.top;
    const plotBottom = padding.top + chartHeight;
    const plotLeft = padding.left;
    const plotRight = padding.left + chartWidth;

    const toY = (percent: number) =>
      plotBottom - ((percent - min) / range) * chartHeight;

    const coords = points.map((point, index) => {
      const x = plotLeft + (index / (points.length - 1)) * chartWidth;
      return {
        ...point,
        x,
        fundY: toY(point.fundPercent),
        benchY: point.benchPercent != null ? toY(point.benchPercent) : null,
        index,
      };
    });

    const fundPath = coords
      .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.fundY}`)
      .join(" ");
    const benchPath =
      showBenchmark && coords.filter((point) => point.benchY != null).length >= 2
        ? coords
            .filter((point) => point.benchY != null)
            .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.benchY}`)
            .join(" ")
        : null;
    const areaPath = `${fundPath} L ${coords[coords.length - 1].x} ${plotBottom} L ${coords[0].x} ${plotBottom} Z`;

    const baselineY =
      max >= 0 && min <= 0 ? toY(0) : null;
    const verticalGridXs = [
      plotLeft,
      plotLeft + chartWidth * 0.25,
      plotLeft + chartWidth * 0.5,
      plotLeft + chartWidth * 0.75,
      plotRight,
    ];
    const midDateIndex = Math.floor((points.length - 1) / 2);

    return {
      width,
      height,
      padding,
      chartWidth,
      chartHeight,
      plotTop,
      plotBottom,
      plotLeft,
      plotRight,
      coords,
      fundPath,
      benchPath,
      areaPath,
      baselineY,
      verticalGridXs,
      min,
      max,
      midDateIndex,
    };
  }, [height, points, showBenchmark]);

  if (!chart) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-400"
        style={{ height }}
      >
        净值数据不足，无法绘制走势图
      </div>
    );
  }

  const isHovering = hoverIndex != null;
  const active = isHovering ? chart.coords[hoverIndex] : null;

  return (
    <div ref={containerRef} className="relative w-full">
      <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="w-full touch-none select-none" role="img">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(61, 126, 255, 0.18)" />
            <stop offset="100%" stopColor="rgba(61, 126, 255, 0.02)" />
          </linearGradient>
        </defs>

        <rect
          x={chart.plotLeft}
          y={chart.plotTop}
          width={chart.chartWidth}
          height={chart.chartHeight}
          fill="none"
          stroke="#e2e8f0"
          strokeWidth={1}
        />
        {chart.verticalGridXs.map((x) => (
          <line
            key={x}
            x1={x}
            y1={chart.plotTop}
            x2={x}
            y2={chart.plotBottom}
            stroke="#e2e8f0"
            strokeWidth={1}
          />
        ))}
        {chart.baselineY != null ? (
          <line
            x1={chart.plotLeft}
            y1={chart.baselineY}
            x2={chart.plotRight}
            y2={chart.baselineY}
            stroke="#cbd5e1"
            strokeWidth={1}
            strokeDasharray="4 4"
          />
        ) : null}

        <path d={chart.areaPath} fill={`url(#${gradientId})`} />
        {chart.benchPath ? (
          <path d={chart.benchPath} fill="none" stroke={BENCH_COLOR} strokeWidth={0.9} />
        ) : null}
        <path d={chart.fundPath} fill="none" stroke={FUND_COLOR} strokeWidth={1} />

        <text x={chart.plotLeft + 4} y={chart.plotTop + 8} fontSize={8} className="fill-slate-400 font-medium tabular-nums">
          {formatSignedPercent(chart.max)}
        </text>
        <text
          x={chart.plotLeft + 4}
          y={chart.plotBottom - 3}
          fontSize={8}
          className="fill-slate-400 font-medium tabular-nums"
        >
          {formatSignedPercent(chart.min)}
        </text>

        {isHovering && active ? (
          <>
            <line
              x1={active.x}
              y1={chart.plotTop}
              x2={active.x}
              y2={chart.plotBottom}
              stroke="#6366f1"
              strokeWidth={1}
              strokeDasharray="4 3"
            />
            <line
              x1={chart.plotLeft}
              y1={active.fundY}
              x2={chart.plotRight}
              y2={active.fundY}
              stroke="#0ea5e9"
              strokeWidth={1}
              strokeDasharray="4 3"
            />
            <circle cx={active.x} cy={active.fundY} r={3} fill={FUND_COLOR} stroke="#fff" strokeWidth={1} />
            {active.benchY != null ? (
              <circle cx={active.x} cy={active.benchY} r={2.5} fill={BENCH_COLOR} stroke="#fff" strokeWidth={1} />
            ) : null}
            <rect
              x={chart.plotLeft + 1}
              y={active.fundY - 8}
              width={40}
              height={14}
              rx={2}
              fill="#ffffff"
              fillOpacity={0.92}
            />
            <text
              x={chart.plotLeft + 4}
              y={active.fundY + 3}
              fontSize={8}
              className="font-semibold tabular-nums"
              fill="#0ea5e9"
            >
              {formatSignedPercent(active.fundPercent)}
            </text>
            <rect
              x={active.x - 17}
              y={chart.plotBottom - 15}
              width={34}
              height={13}
              rx={2}
              fill="#ffffff"
              fillOpacity={0.92}
            />
            <text
              x={active.x}
              y={chart.plotBottom - 5}
              textAnchor="middle"
              fontSize={8}
              className="font-semibold tabular-nums"
              fill="#6366f1"
            >
              {active.date.slice(5)}
            </text>
          </>
        ) : null}

        <text x={chart.plotLeft} y={chart.height - 8} className="fill-slate-400 text-[10px]">
          {points[0].date}
        </text>
        <text
          x={chart.plotLeft + chart.chartWidth / 2}
          y={chart.height - 8}
          textAnchor="middle"
          className="fill-slate-400 text-[10px]"
        >
          {points[chart.midDateIndex].date}
        </text>
        <text
          x={chart.plotRight}
          y={chart.height - 8}
          textAnchor="end"
          className="fill-slate-400 text-[10px]"
        >
          {points[points.length - 1].date}
        </text>

        <rect
          x={chart.plotLeft}
          y={chart.plotTop}
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
    </div>
  );
}
