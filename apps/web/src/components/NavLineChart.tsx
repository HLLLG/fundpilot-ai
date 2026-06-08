"use client";

import { useCallback, useId, useMemo, useRef, useState } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";

export type NavChartPoint = {
  date: string;
  nav: number;
  daily_return_percent?: number | null;
};

type NavLineChartProps = {
  points: NavChartPoint[];
  periodChangePercent?: number | null;
  height?: number;
};

type ChartCoord = {
  x: number;
  y: number;
  value: number;
  date: string;
  dailyReturn: number | null;
  index: number;
};

function formatDateLabel(date: string) {
  if (date.length >= 10) {
    return date.slice(5).replace("-", "/");
  }
  return date;
}

function formatNav(value: number) {
  return value.toFixed(4);
}

function trendFromChange(change: number | null | undefined): "up" | "down" | "flat" {
  if (change === null || change === undefined || Math.abs(change) < 0.01) {
    return "flat";
  }
  return change > 0 ? "up" : "down";
}

const TREND_STYLES = {
  up: {
    line: "#e11d48",
    lineSoft: "#fb7185",
    fillStart: "rgba(225, 29, 72, 0.22)",
    fillEnd: "rgba(225, 29, 72, 0.02)",
    badge: "bg-rose-50 text-rose-700 ring-rose-100",
    dot: "#e11d48",
  },
  down: {
    line: "#059669",
    lineSoft: "#34d399",
    fillStart: "rgba(5, 150, 105, 0.2)",
    fillEnd: "rgba(5, 150, 105, 0.02)",
    badge: "bg-emerald-50 text-emerald-700 ring-emerald-100",
    dot: "#059669",
  },
  flat: {
    line: "#2563eb",
    lineSoft: "#60a5fa",
    fillStart: "rgba(37, 99, 235, 0.18)",
    fillEnd: "rgba(37, 99, 235, 0.02)",
    badge: "bg-slate-100 text-slate-700 ring-slate-200",
    dot: "#2563eb",
  },
} as const;

export function NavLineChart({
  points,
  periodChangePercent,
  height = 240,
}: NavLineChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const computedChange = useMemo(() => {
    if (periodChangePercent !== null && periodChangePercent !== undefined) {
      return periodChangePercent;
    }
    if (points.length < 2 || points[0].nav <= 0) {
      return null;
    }
    const last = points[points.length - 1].nav;
    return Math.round((last / points[0].nav - 1) * 10000) / 100;
  }, [periodChangePercent, points]);

  const trend = trendFromChange(computedChange);
  const colors = TREND_STYLES[trend];
  const latest = points[points.length - 1];

  const chart = useMemo(() => {
    const values = points.map((point) => point.nav);
    if (values.length < 2) {
      return null;
    }

    const width = 640;
    const padding = { top: 20, right: 16, bottom: 36, left: 52 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;

    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const rawRange = rawMax - rawMin || rawMax * 0.01;
    const pad = rawRange * 0.12;
    const min = rawMin - pad;
    const max = rawMax + pad;
    const range = max - min;

    const coords: ChartCoord[] = values.map((value, index) => {
      const x = padding.left + (index / (values.length - 1)) * chartWidth;
      const y = padding.top + chartHeight - ((value - min) / range) * chartHeight;
      return {
        x,
        y,
        value,
        date: points[index].date,
        dailyReturn: points[index].daily_return_percent ?? null,
        index,
      };
    });

    const linePath = coords.map((point) => `${point.x},${point.y}`).join(" ");
    const baselineY =
      padding.top +
      chartHeight -
      ((points[0].nav - min) / range) * chartHeight;
    const areaPath = `${linePath} L ${coords[coords.length - 1].x} ${padding.top + chartHeight} L ${coords[0].x} ${padding.top + chartHeight} Z`;

    let minIdx = 0;
    let maxIdx = 0;
    values.forEach((value, index) => {
      if (value <= values[minIdx]) minIdx = index;
      if (value >= values[maxIdx]) maxIdx = index;
    });

    const yTicks = [min, min + range / 2, max];
    const midDateIndex = Math.floor((points.length - 1) / 2);

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
      yTicks,
      min,
      max,
      range,
      minIdx,
      maxIdx,
      midDateIndex,
    };
  }, [points, height]);

  const pickIndexFromClientX = useCallback(
    (clientX: number) => {
      if (!chart || !containerRef.current) {
        return null;
      }
      const rect = containerRef.current.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const xInViewBox = ratio * chart.width;
      const { padding, chartWidth, coords } = chart;
      if (xInViewBox <= padding.left) {
        return 0;
      }
      if (xInViewBox >= padding.left + chartWidth) {
        return coords.length - 1;
      }
      const relative = (xInViewBox - padding.left) / chartWidth;
      return Math.round(relative * (coords.length - 1));
    },
    [chart],
  );

  const handlePointer = useCallback(
    (clientX: number) => {
      const index = pickIndexFromClientX(clientX);
      if (index !== null) {
        setHoverIndex(index);
      }
    },
    [pickIndexFromClientX],
  );

  if (!chart || !latest) {
    return (
      <div
        className="flex items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 text-sm text-slate-500"
        style={{ height }}
      >
        净值数据不足，无法绘制走势图
      </div>
    );
  }

  const activeIndex = hoverIndex ?? chart.coords.length - 1;
  const active = chart.coords[activeIndex];
  const displayChange =
    hoverIndex !== null && chart.coords[0].value > 0
      ? ((active.value / chart.coords[0].value - 1) * 100)
      : computedChange;

  const changeText =
    displayChange === null || displayChange === undefined
      ? "—"
      : `${displayChange > 0 ? "+" : ""}${displayChange.toFixed(2)}%`;

  return (
    <div className="overflow-hidden rounded-[20px] bg-gradient-to-b from-white via-white to-slate-50/90 ring-1 ring-slate-100">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-wide text-slate-400">
            单位净值
          </p>
          <p className="mt-0.5 text-2xl font-black tabular-nums tracking-tight text-slate-950">
            {formatNav(active.value)}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">{formatDateLabel(active.date)}</p>
        </div>
        <div className="text-right">
          <p className="text-[11px] font-bold text-slate-400">区间涨跌</p>
          <div
            className={`mt-1 inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-sm font-black tabular-nums ring-1 ${colors.badge}`}
          >
            {trend === "up" ? (
              <TrendingUp size={16} aria-hidden />
            ) : trend === "down" ? (
              <TrendingDown size={16} aria-hidden />
            ) : null}
            {changeText}
          </div>
        </div>
      </div>

      <div ref={containerRef} className="relative px-1 pb-2 pt-1">
        <svg
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          className="w-full touch-none select-none"
          role="img"
          aria-label="单位净值走势图"
          onMouseLeave={() => setHoverIndex(null)}
          onMouseMove={(event) => handlePointer(event.clientX)}
          onTouchStart={(event) => {
            const touch = event.touches[0];
            if (touch) {
              handlePointer(touch.clientX);
            }
          }}
          onTouchMove={(event) => {
            const touch = event.touches[0];
            if (touch) {
              handlePointer(touch.clientX);
            }
          }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors.fillStart} />
              <stop offset="100%" stopColor={colors.fillEnd} />
            </linearGradient>
          </defs>

          {chart.yTicks.map((tick, index) => {
            const y =
              chart.padding.top +
              chart.chartHeight -
              ((tick - chart.min) / chart.range) * chart.chartHeight;
            return (
              <g key={index}>
                <line
                  x1={chart.padding.left}
                  y1={y}
                  x2={chart.width - chart.padding.right}
                  y2={y}
                  stroke="#e2e8f0"
                  strokeWidth="1"
                  strokeDasharray="4 4"
                />
                <text
                  x={chart.padding.left - 8}
                  y={y + 3}
                  textAnchor="end"
                  fill="#94a3b8"
                  fontSize="10"
                >
                  {tick.toFixed(4)}
                </text>
              </g>
            );
          })}

          <line
            x1={chart.padding.left}
            y1={chart.baselineY}
            x2={chart.width - chart.padding.right}
            y2={chart.baselineY}
            stroke={colors.lineSoft}
            strokeWidth="1"
            strokeDasharray="6 4"
            opacity="0.55"
          />

          <path d={chart.areaPath} fill={`url(#${gradientId})`} />
          <polyline
            fill="none"
            stroke={colors.line}
            strokeWidth="1"
            strokeLinecap="round"
            strokeLinejoin="round"
            points={chart.linePath}
          />

          {[chart.minIdx, chart.maxIdx].map((index) => {
            const point = chart.coords[index];
            const isLow = index === chart.minIdx;
            return (
              <g key={`extreme-${index}`}>
                <circle
                  cx={point.x}
                  cy={point.y}
                  r="3"
                  fill="white"
                  stroke={isLow ? "#059669" : "#e11d48"}
                  strokeWidth="1.5"
                />
              </g>
            );
          })}

          {hoverIndex !== null ? (
            <g>
              <line
                x1={active.x}
                y1={chart.padding.top}
                x2={active.x}
                y2={chart.padding.top + chart.chartHeight}
                stroke="#94a3b8"
                strokeWidth="1"
                strokeDasharray="3 3"
              />
              <circle
                cx={active.x}
                cy={active.y}
                r="5"
                fill="white"
                stroke={colors.dot}
                strokeWidth="2.5"
              />
            </g>
          ) : (
            <circle
              cx={chart.coords[chart.coords.length - 1].x}
              cy={chart.coords[chart.coords.length - 1].y}
              r="4.5"
              fill="white"
              stroke={colors.dot}
              strokeWidth="2"
            />
          )}

          <rect
            x={chart.padding.left}
            y={chart.padding.top}
            width={chart.width - chart.padding.left - chart.padding.right}
            height={chart.chartHeight}
            fill="transparent"
          />

          <text
            x={chart.padding.left}
            y={chart.height - 10}
            fill="#94a3b8"
            fontSize="10"
          >
            {formatDateLabel(points[0].date)}
          </text>
          <text
            x={chart.padding.left + (chart.width - chart.padding.left - chart.padding.right) / 2}
            y={chart.height - 10}
            textAnchor="middle"
            fill="#94a3b8"
            fontSize="10"
          >
            {formatDateLabel(points[chart.midDateIndex].date)}
          </text>
          <text
            x={chart.width - chart.padding.right}
            y={chart.height - 10}
            textAnchor="end"
            fill="#94a3b8"
            fontSize="10"
          >
            {formatDateLabel(points[points.length - 1].date)}
          </text>
        </svg>

        {hoverIndex !== null ? (
          <div
            className="pointer-events-none absolute z-10 min-w-[120px] rounded-xl border border-slate-200/80 bg-white/95 px-3 py-2 text-xs shadow-lg backdrop-blur-sm"
            style={{
              left: `${(active.x / chart.width) * 100}%`,
              top: 8,
              transform:
                active.x / chart.width > 0.72
                  ? "translateX(-100%)"
                  : active.x / chart.width < 0.28
                    ? "translateX(0)"
                    : "translateX(-50%)",
            }}
          >
            <div className="font-bold text-slate-950">{formatDateLabel(active.date)}</div>
            <div className="mt-1 font-black tabular-nums text-slate-900">
              净值 {formatNav(active.value)}
            </div>
            {active.dailyReturn !== null ? (
              <div
                className={`mt-0.5 font-bold tabular-nums ${
                  active.dailyReturn > 0
                    ? "text-rose-600"
                    : active.dailyReturn < 0
                      ? "text-emerald-600"
                      : "text-slate-500"
                }`}
              >
                日涨跌 {active.dailyReturn > 0 ? "+" : ""}
                {active.dailyReturn.toFixed(2)}%
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-2 text-[10px] text-slate-400">
        <span>拖动或悬停查看每日净值</span>
        <span className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-rose-500" />
            区间高点
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            区间低点
          </span>
        </span>
      </div>
    </div>
  );
}
