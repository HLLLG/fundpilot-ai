"use client";

import { useId, useMemo, useRef, useState } from "react";
import type { PerformanceSeriesPoint } from "@/lib/performanceTrend";
import { formatSignedPercent } from "@/lib/performanceTrend";

const Y_AXIS_HEADROOM_RATIO = 0.12;
const FUND_COLOR = "#3d7eff";
const BENCH_COLOR = "#f59e0b";

export type TradeMarker = {
  date: string; // confirm_date "YYYY-MM-DD"
  kind: "buy" | "sell" | "pending";
  items: { direction: "buy" | "sell"; amount_yuan: number; trade_time: string; status: string }[];
};

type PerformanceReturnChartProps = {
  points: PerformanceSeriesPoint[];
  height?: number;
  showBenchmark?: boolean;
  markers?: TradeMarker[];
};

export function PerformanceReturnChart({
  points,
  height = 220,
  showBenchmark = true,
  markers = [],
}: PerformanceReturnChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [selectedMarkerDate, setSelectedMarkerDate] = useState<string | null>(null);

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

  const markerPoints = useMemo(() => {
    if (!chart || markers.length === 0) {
      return [] as Array<TradeMarker & { x: number; y: number }>;
    }
    const byDate = new Map(chart.coords.map((coord) => [coord.date, coord]));
    return markers
      .map((marker) => {
        const coord = byDate.get(marker.date);
        return coord ? { ...marker, x: coord.x, y: coord.fundY } : null;
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
  const selectedMarker = markerPoints.find((marker) => marker.date === selectedMarkerDate) ?? null;
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
          const isPending = marker.kind === "pending";
          return (
            <circle
              key={marker.date}
              cx={marker.x}
              cy={marker.y}
              r={4}
              fill={isPending ? "#ffffff" : isBuy ? "#f43f5e" : "#10b981"}
              stroke={isPending ? "#94a3b8" : "#ffffff"}
              strokeWidth={1.5}
              style={{ cursor: "pointer" }}
              onClick={() =>
                setSelectedMarkerDate((prev) => (prev === marker.date ? null : marker.date))
              }
            />
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
              key={`marker-control-${marker.date}`}
              type="button"
              onClick={() =>
                setSelectedMarkerDate((current) => (current === marker.date ? null : marker.date))
              }
              className="touch-target inline-flex items-center rounded-full border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700 hover:border-[var(--brand)] hover:text-[var(--brand)]"
              aria-expanded={selectedMarkerDate === marker.date}
            >
              {marker.date.slice(5)} · {marker.kind === "buy" ? "加仓" : marker.kind === "sell" ? "减仓" : "待确认"}
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
              onClick={() => setSelectedMarkerDate(null)}
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
                    {isBuy ? "加仓" : "减仓"}
                    {item.status === "pending" ? "·待确认" : ""}
                  </span>
                  <span className="font-bold tabular-nums text-slate-800">
                    {item.amount_yuan.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
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
