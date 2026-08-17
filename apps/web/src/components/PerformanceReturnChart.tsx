"use client";

import { memo, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { PerformanceSeriesPoint } from "@/lib/performanceTrend";
import { formatSignedPercent } from "@/lib/performanceTrend";
import type { TradeMarker } from "@/lib/tradeMarkers";

const Y_AXIS_HEADROOM_RATIO = 0.12;
const FUND_COLOR = "#3d7eff";
const BENCH_COLOR = "#f59e0b";
const BUY_COLOR = "#c81e3a";
const SELL_COLOR = "#047857";
// formatter 提到模块作用域：交易标记浮层按成交笔数逐条渲染金额。输出格式不变。
const TRADE_AMOUNT_FORMATTER = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
});

export type { TradeMarker };

type PlottedTradeMarker = TradeMarker & { x: number; y: number };

function formatMarkerDateTime(tradeTime: string) {
  return tradeTime.slice(0, 16);
}

function TradeMarkerCallout({
  marker,
  chartWidth,
  chartHeight,
  containerRef,
}: {
  marker: PlottedTradeMarker;
  chartWidth: number;
  chartHeight: number;
  containerRef: RefObject<HTMLDivElement | null>;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [shiftX, setShiftX] = useState(0);
  const showBelow = marker.y < 56;
  const accent = marker.kind === "buy" ? BUY_COLOR : SELL_COLOR;
  const singleItem = marker.items.length === 1;

  useLayoutEffect(() => {
    const card = cardRef.current;
    const parent = containerRef.current;
    if (!card || !parent) {
      return;
    }
    const cardRect = card.getBoundingClientRect();
    const parentRect = parent.getBoundingClientRect();
    const unshiftedLeft = cardRect.left - shiftX;
    const unshiftedRight = cardRect.right - shiftX;
    const pad = 8;
    let next = 0;
    if (unshiftedLeft < parentRect.left + pad) {
      next = parentRect.left + pad - unshiftedLeft;
    } else if (unshiftedRight > parentRect.right - pad) {
      next = parentRect.right - pad - unshiftedRight;
    }
    setShiftX((prev) => (prev === next ? prev : next));
  }, [containerRef, marker.date, marker.kind, marker.x, marker.y, shiftX]);

  return (
    <div
      className="pointer-events-none absolute z-10"
      style={{
        left: `${(marker.x / chartWidth) * 100}%`,
        top: `${(marker.y / chartHeight) * 100}%`,
      }}
    >
      <div
        ref={cardRef}
        className="absolute z-10 w-max min-w-[148px] max-w-[210px] rounded-[14px] bg-white px-3 py-2.5 shadow-[0_12px_28px_rgba(27,75,95,0.16)] ring-1 ring-slate-200/90"
        style={{
          left: 0,
          top: showBelow ? 10 : undefined,
          bottom: showBelow ? undefined : 10,
          transform: `translateX(calc(-50% + ${shiftX}px))`,
        }}
      >
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-semibold tabular-nums tracking-wide text-slate-500">
            {singleItem ? formatMarkerDateTime(marker.items[0].trade_time) : marker.date}
          </p>
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: accent }}
            aria-hidden
          />
        </div>
        <ul className="mt-2 space-y-2">
          {marker.items.map((item, index) => {
            const isBuy = item.direction === "buy";
            return (
              <li key={index} className="flex items-end justify-between gap-3">
                <span
                  className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-bold"
                  style={{
                    color: isBuy ? BUY_COLOR : SELL_COLOR,
                    backgroundColor: isBuy ? "rgba(200, 30, 58, 0.08)" : "rgba(4, 120, 87, 0.1)",
                  }}
                >
                  {isBuy ? "买入" : "卖出"}
                  {item.status === "pending" ? " · 待确认" : ""}
                </span>
                <div className="text-right">
                  <p className="font-display text-[15px] font-black leading-none tabular-nums text-[var(--brand-deep)]">
                    {TRADE_AMOUNT_FORMATTER.format(item.amount_yuan)}
                    <span className="ml-0.5 text-[10px] font-bold text-slate-400">元</span>
                  </p>
                  {singleItem ? null : (
                    <p className="mt-1 text-[10px] tabular-nums text-slate-400">
                      {item.trade_time.slice(11, 16)}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
      <span
        aria-hidden
        className={`absolute z-0 h-2.5 w-2.5 bg-white ${
          showBelow ? "shadow-[-1px_-1px_0_0_#e2e8f0]" : "shadow-[1px_1px_0_0_#e2e8f0]"
        }`}
        style={{
          left: 0,
          top: showBelow ? 10 : -10,
          transform: "translate(-50%, -50%) rotate(45deg)",
        }}
      />
    </div>
  );
}

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
      return [] as PlottedTradeMarker[];
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
      .filter((marker): marker is PlottedTradeMarker => marker != null);
  }, [chart, markers]);

  useEffect(() => {
    if (!selectedMarkerKey) {
      return;
    }
    const dismiss = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setSelectedMarkerKey(null);
      }
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [selectedMarkerKey]);

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
    <div
      ref={containerRef}
      className="relative w-full"
      onPointerLeave={(event) => {
        if (event.pointerType === "mouse") {
          setSelectedMarkerKey(null);
          setHoverIndex(null);
        }
      }}
    >
      <svg
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        className="w-full touch-none select-none rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2"
        role="group"
        aria-label={chartLabel}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setSelectedMarkerKey(null);
            return;
          }
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
              stroke="#7a898f"
              strokeWidth={1}
              strokeDasharray="4 3"
            />
            <line
              x1={chart.plotLeft}
              y1={active.fundY}
              x2={chart.plotRight}
              y2={active.fundY}
              stroke="#b58b45"
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
              fill="#b58b45"
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
              fill="#7a898f"
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
          onPointerDown={() => setSelectedMarkerKey(null)}
          onPointerMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const ratio = (event.clientX - rect.left) / rect.width;
            const index = Math.round(ratio * (chart.coords.length - 1));
            const nextIndex = Math.max(0, Math.min(chart.coords.length - 1, index));
            setHoverIndex(nextIndex);
            if (event.pointerType === "mouse" && selectedMarkerKey) {
              const selectedDate = selectedMarkerKey.slice(0, selectedMarkerKey.indexOf("|"));
              if (chart.coords[nextIndex]?.date !== selectedDate) {
                setSelectedMarkerKey(null);
              }
            }
          }}
          onPointerLeave={(event) => {
            const next = event.relatedTarget;
            if (next instanceof Node && containerRef.current?.contains(next)) {
              return;
            }
            setHoverIndex(null);
            if (event.pointerType === "mouse") {
              setSelectedMarkerKey(null);
            }
          }}
        />
        {markerPoints.map((marker) => {
          const isBuy = marker.kind === "buy";
          const stroke = isBuy ? BUY_COLOR : SELL_COLOR;
          const key = `${marker.date}|${marker.kind}`;
          const activate = () => {
            setSelectedMarkerKey(key);
            const coord = chart.coords.find((item) => item.date === marker.date);
            if (coord) {
              setHoverIndex(coord.index);
            }
          };
          return (
            <g
              key={key}
              aria-label={`${marker.date} ${isBuy ? "买入" : "卖出"}${marker.pending ? "，待确认" : ""}`}
              className="outline-none"
              style={{ cursor: "pointer", outline: "none" }}
              onPointerEnter={(event) => {
                if (event.pointerType === "mouse") {
                  activate();
                }
              }}
              onPointerDown={(event) => {
                event.preventDefault();
                event.stopPropagation();
                activate();
              }}
              onPointerLeave={(event) => {
                if (event.pointerType === "mouse") {
                  setSelectedMarkerKey(null);
                }
              }}
            >
              <circle cx={marker.x} cy={marker.y} r={16} fill="transparent" />
              <circle
                cx={marker.x}
                cy={marker.y}
                r={4}
                fill={marker.pending ? "#ffffff" : stroke}
                stroke={marker.pending ? stroke : "#ffffff"}
                strokeWidth={1.35}
              />
            </g>
          );
        })}
      </svg>

      <p className="sr-only" aria-live="polite">
        {selectedMarker
          ? `${selectedMarker.kind === "buy" ? "买入" : "卖出"} ${selectedMarker.items
              .map((item) => `${TRADE_AMOUNT_FORMATTER.format(item.amount_yuan)}元 ${formatMarkerDateTime(item.trade_time)}`)
              .join("，")}`
          : active
            ? `${active.date}，基金收益${formatSignedPercent(active.fundPercent)}${
                active.benchPercent != null
                  ? `，对比基准${formatSignedPercent(active.benchPercent)}`
                  : ""
              }`
            : ""}
      </p>

      {selectedMarker ? (
        <TradeMarkerCallout
          marker={selectedMarker}
          chartWidth={chart.width}
          chartHeight={chart.height}
          containerRef={containerRef}
        />
      ) : null}
    </div>
  );
}

// 手写 SVG 图表的路径计算与 hover 状态都不便宜，父面板任何无关状态变化都会
// 触发重算。props 已在父级稳定为同一引用，这里用 memo 把它们挡在重渲染之外。
export const PerformanceReturnChart = memo(PerformanceReturnChartView);
