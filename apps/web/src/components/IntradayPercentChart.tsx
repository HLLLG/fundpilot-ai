"use client";

import { useId, useMemo, useRef, useState } from "react";
import { clockToSessionRatio } from "@/lib/intradayChartTime";

export type IntradayPoint = {
  time: string;
  percent: number;
};

type IntradayPercentChartProps = {
  points: IntradayPoint[];
  height?: number;
  /** 数据源没有真实分时序列、只拿到当日板块涨跌这一个数字时，用虚线水平线代替空白占位，
   * 并区别于真实分时曲线（无面积填充、无 hover 时间提示）。 */
  flat?: boolean;
};

/** 数据源查不到分时明细、只有当日板块涨跌这一个数字时，合成一条贯穿全交易时段的水平线，
 * 比"暂无分时数据"占位更直观地传达"我们确实有今天的涨跌幅，只是没有分时走势"。 */
export function buildFlatIntradayPoints(percent: number): IntradayPoint[] {
  return [
    { time: "09:30", percent },
    { time: "15:00", percent },
  ];
}

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

/** Y 轴上下各预留约 15% 空位，避免极值贴边。 */
const Y_AXIS_HEADROOM_RATIO = 0.15;

/** 养基宝同款 Y 轴：max(|最高|, |最低|)，以 0 为中心对称 [-span, +span]。 */
function computeSymmetricSpan(points: IntradayPoint[]): number {
  const values = points.map((point) => point.percent);
  if (!values.length) {
    return 0.15;
  }
  const peak = Math.max(Math.abs(Math.max(...values)), Math.abs(Math.min(...values)));
  const padded = peak * (1 + Y_AXIS_HEADROOM_RATIO);
  return Math.max(padded, 0.1);
}

const CROSSHAIR = {
  vertical: "#6366f1",
  horizontal: "#0ea5e9",
};

export function IntradayPercentChart({ points, height = 200, flat = false }: IntradayPercentChartProps) {
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
      const sessionRatio = clockToSessionRatio(point.time);
      const x = padding.left + sessionRatio * chartWidth;
      const rawY = plotBottom - ((point.percent - min) / range) * chartHeight;
      const y = Math.max(plotTop, Math.min(plotBottom, rawY));
      return { ...point, x, y, index, sessionRatio };
    });

    const linePath = coords.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
    const baselineY = padding.top + chartHeight / 2;
    const plotLeft = padding.left;
    const plotRight = padding.left + chartWidth;
    const verticalGridXs = [
      plotLeft,
      plotLeft + chartWidth * 0.25,
      plotLeft + chartWidth * 0.5,
      plotLeft + chartWidth * 0.75,
      plotRight,
    ];
    const areaPath = `${linePath} L ${coords[coords.length - 1].x} ${plotBottom} L ${coords[0].x} ${plotBottom} Z`;
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
      plotTop,
      plotBottom,
      plotLeft,
      plotRight,
      verticalGridXs,
      halfSpan,
      colors,
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

  const isHovering = hoverIndex != null;
  const active = isHovering ? chart.coords[hoverIndex] : null;
  const yLabelX = chart.plotLeft + 4;
  const yTopLabelY = chart.plotTop + 8;
  const yBottomLabelY = chart.plotBottom - 3;

  return (
    <div ref={containerRef} className="relative w-full">
      <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="w-full" role="img">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={chart.colors.fillStart} />
            <stop offset="100%" stopColor={chart.colors.fillEnd} />
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
        <line
          x1={chart.plotLeft}
          y1={chart.baselineY}
          x2={chart.plotRight}
          y2={chart.baselineY}
          stroke="#cbd5e1"
          strokeWidth={1}
          strokeDasharray="4 4"
        />
        {flat ? null : <path d={chart.areaPath} fill={`url(#${gradientId})`} />}
        <path
          d={chart.linePath}
          fill="none"
          stroke={chart.colors.line}
          strokeWidth={flat ? 1.2 : 0.9}
          strokeDasharray={flat ? "5 4" : undefined}
        />

        <text
          x={yLabelX}
          y={yTopLabelY}
          textAnchor="start"
          className="fill-slate-400 font-medium tabular-nums"
          fontSize={8}
        >
          {formatRangePercent(chart.halfSpan)}
        </text>
        <text
          x={yLabelX}
          y={yBottomLabelY}
          textAnchor="start"
          className="fill-slate-400 font-medium tabular-nums"
          fontSize={8}
        >
          {formatRangePercent(-chart.halfSpan)}
        </text>

        {isHovering && active ? (
          <>
            <line
              x1={active.x}
              y1={chart.plotTop}
              x2={active.x}
              y2={chart.plotBottom}
              stroke={CROSSHAIR.vertical}
              strokeWidth={1}
              strokeDasharray="4 3"
            />
            <line
              x1={chart.plotLeft}
              y1={active.y}
              x2={chart.plotRight}
              y2={active.y}
              stroke={CROSSHAIR.horizontal}
              strokeWidth={1}
              strokeDasharray="4 3"
            />
            <circle cx={active.x} cy={active.y} r={3} fill={chart.colors.line} stroke="#fff" strokeWidth={1} />
            <rect
              x={chart.plotLeft + 1}
              y={active.y - 8}
              width={40}
              height={14}
              rx={2}
              fill="#ffffff"
              fillOpacity={0.92}
            />
            <text
              x={yLabelX}
              y={active.y + 3}
              textAnchor="start"
              className="font-semibold tabular-nums"
              fontSize={8}
              fill={CROSSHAIR.horizontal}
            >
              {formatRangePercent(active.percent)}
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
              className="font-semibold tabular-nums"
              fontSize={8}
              fill={CROSSHAIR.vertical}
            >
              {formatTimeLabel(active.time)}
            </text>
          </>
        ) : null}

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
          x={chart.plotLeft}
          y={chart.plotTop}
          width={chart.chartWidth}
          height={chart.chartHeight}
          fill="transparent"
          onMouseMove={(event) => {
            if (flat) {
              return;
            }
            const rect = event.currentTarget.getBoundingClientRect();
            const ratio = (event.clientX - rect.left) / rect.width;
            let bestIndex = 0;
            let bestDistance = Number.POSITIVE_INFINITY;
            for (const point of chart.coords) {
              const distance = Math.abs(point.sessionRatio - ratio);
              if (distance < bestDistance) {
                bestDistance = distance;
                bestIndex = point.index;
              }
            }
            setHoverIndex(bestIndex);
          }}
          onMouseLeave={() => setHoverIndex(null)}
        />
      </svg>
    </div>
  );
}
