"use client";

import { memo, useId, useMemo, useRef, useState } from "react";
import type { PerformanceSeriesPoint } from "@/lib/performanceTrend";
import { formatSignedPercent } from "@/lib/performanceTrend";
import type { TradeMarker } from "@/lib/tradeMarkers";

const Y_AXIS_HEADROOM_RATIO = 0.12;
const FUND_COLOR = "#3d7eff";
const BENCH_COLOR = "#f59e0b";
const BUY_COLOR = "#e11d48";
const SELL_COLOR = "#059669";
// formatter 提到模块作用域：交易标记浮层按成交笔数逐条渲染金额。输出格式不变。
const TRADE_AMOUNT_FORMATTER = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
});

export type { TradeMarker };

type PerformanceReturnChartProps = {
  points: PerformanceSeriesPoint[];
  height?: number;
  showBenchmark?: boolean;
  markers?: TradeMarker[];
};

function PerformanceReturnChartView({
  points,
  height = 220,
  showBenchmark = true,
  markers = [],
}: PerformanceReturnChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [selectedMarkerKey, setSelectedMarkerKey] = useState<string | null>(null);

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

    const padding = { top: markers.length > 0 ? 28 : 14, right: 10, bottom: 26, left: 8 };
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
  }, [height, markers.length, points, showBenchmark]);

  const markerPoints = useMemo(() => {
    if (!chart || markers.length === 0) {
      return [] as Array<TradeMarker & { x: number; y: number }>;
    }
    const byDate = new Map(chart.coords.map((coord) => [coord.date, coord]));
    const kindsByDate = new Map<string, number>();
    for (const marker of markers) {
      kindsByDate.set(marker.date, (kindsByDate.get(marker.date) ?? 0) + 1);
    }
    return markers
      .map((marker) => {
        const coord = byDate.get(marker.date);
        if (!coord) {
          return null;
        }
        const overlap = (kindsByDate.get(marker.date) ?? 1) > 1;
        const x = overlap ? coord.x + (marker.kind === "buy" ? -6 : 6) : coord.x;
        return { ...marker, x, y: coord.fundY };
      })
      .filter((marker): marker is TradeMarker & { x: number; y: number } => marker != null);
  }, [chart, markers]);

  if (!chart) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500"
        style={{ height }}
      >
        净值数据不足，无法绘制走势图
      </div>
    );
  }

  const isHovering = hoverIndex != null;
  const active = isHovering ? chart.coords[hoverIndex] : null;
  const selectedMarker =
    markerPoints.find((marker) => `${marker.date}|${marker.kind}` === selectedMarkerKey) ?? null;
  const latest = chart.coords[chart.coords.length - 1];
  const chartLabel = `基金累计收益走势图，${chart.coords[0].date}至${latest.date}，最新基金收益${formatSignedPercent(latest.fundPercent)}${
    showBenchmark && latest.benchPercent != null
      ? `，对比基准${formatSignedPercent(latest.benchPercent)}`
      : ""
  }。聚焦后可用左右方向键逐日查看`;

  const moveKeyboardCursor = (key: string) => {
    if (key === "Home") {
      setHoverIndex(0);
      return;
    }
    if (key === "End") {
      setHoverIndex(chart.coords.length - 1);
      return;
    }
    if (key === "ArrowLeft") {
      setHoverIndex((current) => Math.max(0, (current ?? chart.coords.length) - 1));
      return;
    }
    if (key === "ArrowRight") {
      setHoverIndex((current) => Math.min(chart.coords.length - 1, (current ?? -1) + 1));
    }
  };

  return (
    <div ref={containerRef} className="relative w-full">
      <svg
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        className="w-full touch-none select-none rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2"
        role="img"
        aria-label={chartLabel}
        tabIndex={0}
        onKeyDown={(event) => {
          if (["Home", "End", "ArrowLeft", "ArrowRight"].includes(event.key)) {
            event.preventDefault();
            moveKeyboardCursor(event.key);
          }
        }}
        onBlur={() => setHoverIndex(null)}
      >
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

        {markerPoints.map((marker) => {
          const isBuy = marker.kind === "buy";
          const fill = marker.pending ? "#ffffff" : isBuy ? BUY_COLOR : SELL_COLOR;
          const stroke = isBuy ? BUY_COLOR : SELL_COLOR;
          const label = isBuy ? "买入" : "卖出";
          const tagWidth = 28;
          const tagHeight = 13;
          const tagX = Math.min(
            chart.plotRight - tagWidth,
            Math.max(chart.plotLeft, marker.x - tagWidth / 2),
          );
          const above = isBuy || marker.y > chart.plotTop + 28;
          const tagY = above
            ? Math.max(chart.plotTop - 2, marker.y - 22)
            : Math.min(chart.plotBottom - tagHeight, marker.y + 8);
          const pointerY = above ? tagY + tagHeight : tagY;
          const pointerTipY = above ? marker.y - 4 : marker.y + 4;
          return (
            <g
              key={`${marker.date}-${marker.kind}`}
              style={{ cursor: "pointer" }}
              onClick={() =>
                setSelectedMarkerKey((prev) => {
                  const key = `${marker.date}|${marker.kind}`;
                  return prev === key ? null : key;
                })
              }
            >
              <circle
                cx={marker.x}
                cy={marker.y}
                r={3}
                fill={stroke}
                stroke="#ffffff"
                strokeWidth={1.25}
              />
              <path
                d={`M ${marker.x - 3.5} ${pointerY} L ${marker.x + 3.5} ${pointerY} L ${marker.x} ${pointerTipY} Z`}
                fill={fill}
                stroke={stroke}
                strokeWidth={0.6}
              />
              <rect
                x={tagX}
                y={tagY}
                width={tagWidth}
                height={tagHeight}
                rx={2.5}
                fill={fill}
                stroke={stroke}
                strokeWidth={0.8}
              />
              <text
                x={tagX + tagWidth / 2}
                y={tagY + 9.5}
                textAnchor="middle"
                fontSize={8}
                fontWeight={700}
                fill={marker.pending ? stroke : "#ffffff"}
              >
                {label}
              </text>
            </g>
          );
        })}

        <text x={chart.plotLeft + 4} y={chart.plotTop + 8} fontSize={8} className="fill-slate-500 font-medium tabular-nums">
          {formatSignedPercent(chart.max)}
        </text>
        <text
          x={chart.plotLeft + 4}
          y={chart.plotBottom - 3}
          fontSize={8}
          className="fill-slate-500 font-medium tabular-nums"
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

        <text x={chart.plotLeft} y={chart.height - 8} className="fill-slate-500 text-[10px]">
          {points[0].date}
        </text>
        <text
          x={chart.plotLeft + chart.chartWidth / 2}
          y={chart.height - 8}
          textAnchor="middle"
          className="fill-slate-500 text-[10px]"
        >
          {points[chart.midDateIndex].date}
        </text>
        <text
          x={chart.plotRight}
          y={chart.height - 8}
          textAnchor="end"
          className="fill-slate-500 text-[10px]"
        >
          {points[points.length - 1].date}
        </text>

        <rect
          x={chart.plotLeft}
          y={chart.plotTop}
          width={chart.chartWidth}
          height={chart.chartHeight}
          fill="transparent"
          onPointerMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const ratio = (event.clientX - rect.left) / rect.width;
            const index = Math.round(ratio * (chart.coords.length - 1));
            setHoverIndex(Math.max(0, Math.min(chart.coords.length - 1, index)));
          }}
          onPointerLeave={() => setHoverIndex(null)}
        />
      </svg>

      <p className="sr-only" aria-live="polite">
        {active
          ? `${active.date}，基金收益${formatSignedPercent(active.fundPercent)}${
              active.benchPercent != null
                ? `，对比基准${formatSignedPercent(active.benchPercent)}`
                : ""
            }`
          : ""}
      </p>

      {markerPoints.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-2" aria-label="交易记录日期">
          {markerPoints.map((marker) => (
            <button
              key={`marker-control-${marker.date}-${marker.kind}`}
              type="button"
              onClick={() =>
                setSelectedMarkerKey((current) => {
                  const key = `${marker.date}|${marker.kind}`;
                  return current === key ? null : key;
                })
              }
              className={`touch-target inline-flex items-center rounded-full border bg-white px-3 text-xs font-bold hover:border-[var(--brand)] hover:text-[var(--brand)] ${
                marker.kind === "buy"
                  ? "border-rose-200 text-rose-700"
                  : "border-emerald-200 text-emerald-700"
              }`}
              aria-expanded={selectedMarkerKey === `${marker.date}|${marker.kind}`}
            >
              {marker.date.slice(5)} · {marker.kind === "buy" ? "买入" : "卖出"}
              {marker.pending ? "·待确认" : ""}
            </button>
          ))}
        </div>
      ) : null}

      {selectedMarker ? (
        <div
          className="absolute top-1 z-10 w-44 -translate-x-1/2 rounded-xl border border-slate-200 bg-white p-2.5 text-xs shadow-lg"
          style={{
            left: `${Math.min(82, Math.max(18, (selectedMarker.x / chart.width) * 100))}%`,
          }}
        >
          <div className="mb-1 flex items-center justify-between">
            <span className="font-bold text-slate-700">{selectedMarker.date}</span>
            <button
              type="button"
              onClick={() => setSelectedMarkerKey(null)}
              className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
          <ul className="space-y-1">
            {selectedMarker.items.map((item, index) => {
              const isBuy = item.direction === "buy";
              return (
                <li key={index} className="flex items-center justify-between gap-2">
                  <span
                    className={`shrink-0 rounded px-1 py-0.5 text-[10px] font-bold ${
                      isBuy ? "bg-rose-100 profit-up" : "bg-emerald-100 profit-down"
                    }`}
                  >
                    {isBuy ? "买入" : "卖出"}
                    {item.status === "pending" ? "·待确认" : ""}
                  </span>
                  <span className="font-bold tabular-nums text-slate-800">
                    {TRADE_AMOUNT_FORMATTER.format(item.amount_yuan)}
                  </span>
                  <span className="shrink-0 text-[10px] tabular-nums text-slate-500">
                    {item.trade_time.slice(5, 16)}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

// 手写 SVG 图表的路径计算与 hover 状态都不便宜，父面板任何无关状态变化都会
// 触发重算。props 已在父级稳定为同一引用，这里用 memo 把它们挡在重渲染之外。
export const PerformanceReturnChart = memo(PerformanceReturnChartView);
