"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ProfitTrend } from "@/lib/api";
import { clockToSessionRatio } from "@/lib/intradayChartTime";

const INDEX_COLOR = "#5B8DEF";
const AXIS_FONT_SIZE = 10;
const AXIS_LABEL_CLASS = "fill-slate-400 tabular-nums";
const DEFAULT_CHART_WIDTH = 720;
const MIN_CHART_WIDTH = 280;

type ProfitAnalysisTrendChartProps = {
  trend: ProfitTrend | null | undefined;
  height?: number;
};

function finiteChartValue(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function mapProfitTrendValues(points: ProfitTrend["points"]) {
  return points.map((point) => ({
    portfolioPercent: finiteChartValue(point.portfolio_percent),
    indexPercent: finiteChartValue(point.index_percent),
  }));
}

export function buildSegmentedLinePath(points: Array<{ x: number; y: number | null }>): string {
  let drawing = false;
  const commands: string[] = [];
  for (const point of points) {
    if (point.y == null) {
      drawing = false;
      continue;
    }
    commands.push(`${drawing ? "L" : "M"} ${point.x} ${point.y}`);
    drawing = true;
  }
  return commands.join(" ");
}

function buildSegmentedAreaPath(
  points: Array<{ x: number; y: number | null }>,
  baselineY: number,
): string {
  const segments: Array<Array<{ x: number; y: number }>> = [];
  let segment: Array<{ x: number; y: number }> = [];

  const flush = () => {
    if (segment.length >= 2) {
      segments.push(segment);
    }
    segment = [];
  };

  for (const point of points) {
    if (point.y == null) {
      flush();
      continue;
    }
    segment.push({ x: point.x, y: point.y });
  }
  flush();

  return segments
    .map((current) => {
      const line = current
        .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
        .join(" ");
      const first = current[0];
      const last = current[current.length - 1];
      return `${line} L ${last.x} ${baselineY} L ${first.x} ${baselineY} Z`;
    })
    .join(" ");
}

function formatPercent(value: number | null | undefined) {
  const finiteValue = finiteChartValue(value);
  if (finiteValue == null) {
    return "—";
  }
  const rounded = Math.round(finiteValue * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

const TARGET_Y_INTERVALS = 6;
const MIN_Y_AXIS_STEP = 0.01;

export function formatProfitAxisLabel(value: number) {
  const rounded = Math.round(value * 100) / 100;
  if (Math.abs(rounded) < 0.005) {
    return "0.00%";
  }
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

function niceAxisStep(rawStep: number) {
  const safeStep = Math.max(rawStep, MIN_Y_AXIS_STEP);
  const exponent = Math.floor(Math.log10(safeStep));
  const scale = 10 ** exponent;
  const fraction = safeStep / scale;
  const niceFraction =
    fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return Math.max(MIN_Y_AXIS_STEP, niceFraction * scale);
}

function roundAxisValue(value: number) {
  return Math.round(value * 1_000_000) / 1_000_000;
}

/** 按组合收益与上证曲线的实际跨度生成约 6 个“整步长”区间，并始终保留零轴。 */
export function computeProfitAxis(values: number[]) {
  const finiteValues = values.filter((value) => Number.isFinite(value));
  if (finiteValues.length === 0 || finiteValues.every((value) => Math.abs(value) < 0.005)) {
    return { min: -0.75, max: 0.75, step: 0.25 };
  }

  const dataMin = Math.min(...finiteValues, 0);
  const dataMax = Math.max(...finiteValues, 0);
  const span = dataMax - dataMin;
  const padding = span * 0.08;
  const step = niceAxisStep((span + padding * 2) / TARGET_Y_INTERVALS);
  const min = Math.floor((dataMin - padding) / step) * step;
  const max = Math.ceil((dataMax + padding) / step) * step;

  return {
    min: roundAxisValue(Math.min(min, -step)),
    max: roundAxisValue(Math.max(max, step)),
    step: roundAxisValue(step),
  };
}

function buildYTicks(min: number, max: number, step: number) {
  const ticks: number[] = [];
  const count = Math.round((max - min) / step);
  for (let index = 0; index <= count; index += 1) {
    ticks.push(roundAxisValue(min + index * step));
  }
  return ticks;
}

function leftPaddingForLabels(maxAbs: number) {
  const sample = formatProfitAxisLabel(maxAbs);
  return Math.max(46, sample.length * 5.8 + 10);
}

function portfolioColors(latest: number) {
  if (latest > 0.005) {
    return {
      line: "#e11d48",
      fillStart: "rgba(225,29,72,0.18)",
      fillEnd: "rgba(225,29,72,0.02)",
    };
  }
  if (latest < -0.005) {
    return {
      line: "#059669",
      fillStart: "rgba(5,150,105,0.16)",
      fillEnd: "rgba(5,150,105,0.02)",
    };
  }
  return {
    line: "#64748b",
    fillStart: "rgba(100,116,139,0.1)",
    fillEnd: "rgba(100,116,139,0.01)",
  };
}

export function ProfitAnalysisTrendChart({ trend, height = 200 }: ProfitAnalysisTrendChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState<number | null>(null);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) {
      return;
    }

    const updateWidth = (nextWidth?: number) => {
      const measured = nextWidth ?? node.getBoundingClientRect().width;
      if (!Number.isFinite(measured) || measured <= 0) {
        return;
      }
      const rounded = Math.round(measured);
      setContainerWidth((current) => (current === rounded ? current : rounded));
    };

    updateWidth();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver((entries) => {
        updateWidth(entries[0]?.contentRect.width);
      });
      observer.observe(node);
      return () => observer.disconnect();
    }

    const handleResize = () => updateWidth();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const responsiveWidth = Math.max(
    MIN_CHART_WIDTH,
    containerWidth ?? DEFAULT_CHART_WIDTH,
  );

  const chart = useMemo(() => {
    const points = trend?.points ?? [];
    if (points.length < 2) {
      return null;
    }

    const mappedValues = mapProfitTrendValues(points);
    const portfolioValues = mappedValues
      .map((point) => point.portfolioPercent)
      .filter((value): value is number => value != null);
    const indexValues = mappedValues
      .map((point) => point.indexPercent)
      .filter((value): value is number => value != null);
    const hasContinuousSeries = mappedValues.some((point, index) => {
      if (index === 0) {
        return false;
      }
      const previous = mappedValues[index - 1];
      return (
        (point.portfolioPercent != null && previous.portfolioPercent != null) ||
        (point.indexPercent != null && previous.indexPercent != null)
      );
    });
    if (!hasContinuousSeries) {
      return null;
    }
    const axisValues = [...portfolioValues, ...indexValues];
    const { min, max, step } = computeProfitAxis(axisValues);
    const yTickValues = buildYTicks(min, max, step);
    const leftPad = leftPaddingForLabels(Math.max(Math.abs(min), Math.abs(max)));
    const padding = { top: 12, right: 10, bottom: 14, left: leftPad };
    const width = responsiveWidth;
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const range = max - min || 1;
    const plotTop = padding.top;
    const plotBottom = padding.top + chartHeight;
    const plotLeft = padding.left;
    const plotRight = padding.left + chartWidth;

    const toY = (percent: number) => plotBottom - ((percent - min) / range) * chartHeight;

    const coords = points.map((point, index) => {
      const mapped = mappedValues[index];
      const sessionRatio =
        trend?.kind === "intraday"
          ? clockToSessionRatio(point.time ?? "09:30")
          : points.length > 1
            ? index / (points.length - 1)
            : 0;
      const x =
        trend?.kind === "intraday"
          ? plotLeft + sessionRatio * chartWidth
          : plotLeft + sessionRatio * chartWidth;
      return {
        ...point,
        x,
        sessionRatio,
        portfolioY:
          mapped.portfolioPercent != null ? toY(mapped.portfolioPercent) : null,
        indexY: mapped.indexPercent != null ? toY(mapped.indexPercent) : null,
        index,
      };
    });

    const portfolioPath = buildSegmentedLinePath(
      coords.map((point) => ({ x: point.x, y: point.portfolioY })),
    );
    const portfolioArea = buildSegmentedAreaPath(
      coords.map((point) => ({ x: point.x, y: point.portfolioY })),
      plotBottom,
    );
    const indexPath = buildSegmentedLinePath(
      coords.map((point) => ({ x: point.x, y: point.indexY })),
    );

    const baselineY = toY(0);
    const yTicks = yTickValues.map((value) => ({
      value,
      y: toY(value),
      label: formatProfitAxisLabel(value),
      isZero: Math.abs(value) < MIN_Y_AXIS_STEP / 2,
    }));

    const xLabels =
      trend?.kind === "intraday"
        ? ["09:30", "11:30/13:00", "15:00"]
        : [
            (points[0].date ?? "").slice(5),
            (points[points.length - 1].date ?? "").slice(5),
          ];

    const latestPortfolio = portfolioValues[portfolioValues.length - 1] ?? 0;
    const colors = portfolioColors(latestPortfolio);
    let latestDataIndex = coords.length - 1;
    while (
      latestDataIndex > 0 &&
      coords[latestDataIndex].portfolioY == null &&
      coords[latestDataIndex].indexY == null
    ) {
      latestDataIndex -= 1;
    }

    return {
      width,
      height,
      coords,
      portfolioPath,
      portfolioArea,
      indexPath,
      baselineY,
      yTicks,
      xLabels,
      padding,
      plotLeft,
      plotRight,
      plotTop,
      plotBottom,
      chartWidth,
      colors,
      latestDataIndex,
    };
  }, [height, responsiveWidth, trend]);

  if (!chart) {
    return (
      <div
        ref={containerRef}
        className="flex items-center justify-center rounded-xl bg-slate-50 text-sm text-slate-500"
        style={{ height }}
      >
        暂无走势数据
      </div>
    );
  }

  const active =
    hoverIndex != null ? chart.coords[hoverIndex] : chart.coords[chart.latestDataIndex];
  const interactiveIndices = chart.coords
    .filter((point) => point.portfolioY != null || point.indexY != null)
    .map((point) => point.index);
  const latest = chart.coords[chart.latestDataIndex];
  const latestLabel = latest
    ? `${latest.time ?? latest.date ?? "最新数据"}，组合${formatPercent(latest.portfolio_percent)}，上证${formatPercent(latest.index_percent)}`
    : "暂无可读数据";
  const chartLabel = `收益走势图，${latestLabel}。聚焦后可用左右方向键逐点查看`;

  const moveKeyboardCursor = (key: string) => {
    if (interactiveIndices.length === 0) {
      return;
    }
    if (key === "Home") {
      setHoverIndex(interactiveIndices[0]);
      return;
    }
    if (key === "End") {
      setHoverIndex(interactiveIndices[interactiveIndices.length - 1]);
      return;
    }

    const currentPosition = hoverIndex == null ? -1 : interactiveIndices.indexOf(hoverIndex);
    if (key === "ArrowRight") {
      const nextPosition = Math.min(interactiveIndices.length - 1, currentPosition + 1);
      setHoverIndex(interactiveIndices[Math.max(0, nextPosition)]);
    } else if (key === "ArrowLeft") {
      const fallbackPosition = currentPosition < 0 ? interactiveIndices.length : currentPosition;
      setHoverIndex(interactiveIndices[Math.max(0, fallbackPosition - 1)]);
    }
  };

  return (
    <div ref={containerRef} className="relative w-full select-none">
      <svg
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        width="100%"
        height={height}
        preserveAspectRatio="none"
        className="block w-full overflow-visible rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2"
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
            <stop offset="0%" stopColor={chart.colors.fillStart} />
            <stop offset="100%" stopColor={chart.colors.fillEnd} />
          </linearGradient>
          <clipPath id={`${gradientId}-plot`}>
            <rect
              x={chart.plotLeft}
              y={chart.plotTop}
              width={chart.chartWidth}
              height={chart.plotBottom - chart.plotTop}
              rx={4}
            />
          </clipPath>
        </defs>

        <rect
          x={chart.plotLeft}
          y={chart.plotTop}
          width={chart.chartWidth}
          height={chart.plotBottom - chart.plotTop}
          fill="#fafbfc"
          stroke="#e8ecf1"
          strokeWidth={1}
          rx={4}
        />

        {chart.yTicks.map((tick) => (
          <g key={tick.value}>
            <line
              x1={chart.plotLeft}
              y1={tick.y}
              x2={chart.plotRight}
              y2={tick.y}
              stroke={tick.isZero ? "#dbe1ea" : "#eef1f5"}
              strokeWidth={1}
              strokeDasharray={tick.isZero ? "3 3" : undefined}
            />
            <text
              x={chart.plotLeft - 8}
              y={tick.y + 3}
              textAnchor="end"
              className={AXIS_LABEL_CLASS}
              style={{ fontSize: AXIS_FONT_SIZE, fontWeight: 600 }}
            >
              {tick.label}
            </text>
          </g>
        ))}

        {chart.portfolioArea ? (
          <path
            d={chart.portfolioArea}
            fill={`url(#${gradientId})`}
            clipPath={`url(#${gradientId}-plot)`}
          />
        ) : null}
        {chart.indexPath ? (
          <path
            d={chart.indexPath}
            fill="none"
            stroke={INDEX_COLOR}
            strokeWidth={0.9}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={0.85}
            clipPath={`url(#${gradientId}-plot)`}
          />
        ) : null}
        {chart.portfolioPath ? (
          <path
            d={chart.portfolioPath}
            fill="none"
            stroke={chart.colors.line}
            strokeWidth={1.1}
            strokeLinecap="round"
            strokeLinejoin="round"
            clipPath={`url(#${gradientId}-plot)`}
          />
        ) : null}

        {active ? (
          <>
            <line
              x1={active.x}
              y1={chart.plotTop}
              x2={active.x}
              y2={chart.plotBottom}
              stroke="#94a3b8"
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.7}
            />
            {active.portfolioY != null ? (
              <circle
                cx={active.x}
                cy={active.portfolioY}
                r="2.5"
                fill="#fff"
                stroke={chart.colors.line}
                strokeWidth={1.1}
              />
            ) : null}
            {active.indexY != null ? (
              <circle cx={active.x} cy={active.indexY} r="2" fill="#fff" stroke={INDEX_COLOR} strokeWidth={1} />
            ) : null}
          </>
        ) : null}

        <rect
          x={chart.plotLeft}
          y={chart.plotTop}
          width={chart.chartWidth}
          height={chart.plotBottom - chart.plotTop}
          fill="transparent"
          onMouseMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const ratio = (event.clientX - rect.left) / rect.width;
            let bestIndex = 0;
            let bestDistance = Number.POSITIVE_INFINITY;
            for (const point of chart.coords) {
              if (point.portfolioY == null && point.indexY == null) {
                continue;
              }
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

        {trend?.kind === "intraday" ? (
          <>
            <text
              x={chart.plotLeft}
              y={chart.height - 4}
              className={AXIS_LABEL_CLASS}
              style={{ fontSize: AXIS_FONT_SIZE, fontWeight: 600 }}
            >
              09:30
            </text>
            <text
              x={chart.plotLeft + chart.chartWidth / 2}
              y={chart.height - 4}
              textAnchor="middle"
              className={AXIS_LABEL_CLASS}
              style={{ fontSize: AXIS_FONT_SIZE, fontWeight: 600 }}
            >
              11:30/13:00
            </text>
            <text
              x={chart.plotRight}
              y={chart.height - 4}
              textAnchor="end"
              className={AXIS_LABEL_CLASS}
              style={{ fontSize: AXIS_FONT_SIZE, fontWeight: 600 }}
            >
              15:00
            </text>
          </>
        ) : (
          <>
            <text
              x={chart.plotLeft}
              y={chart.height - 4}
              className={AXIS_LABEL_CLASS}
              style={{ fontSize: AXIS_FONT_SIZE, fontWeight: 600 }}
            >
              {chart.xLabels[0]}
            </text>
            <text
              x={chart.plotRight}
              y={chart.height - 4}
              textAnchor="end"
              className={AXIS_LABEL_CLASS}
              style={{ fontSize: AXIS_FONT_SIZE, fontWeight: 600 }}
            >
              {chart.xLabels[chart.xLabels.length - 1]}
            </text>
          </>
        )}
      </svg>

      {hoverIndex != null && active ? (
        <div className="pointer-events-none absolute left-1/2 top-2 -translate-x-1/2 rounded-lg border border-slate-200/80 bg-white/95 px-2.5 py-1.5 text-[11px] font-bold shadow-sm backdrop-blur-sm">
          <span style={{ color: chart.colors.line }}>我的 {formatPercent(active.portfolio_percent)}</span>
          <span className="mx-1.5 text-slate-500">·</span>
          <span style={{ color: INDEX_COLOR }}>上证 {formatPercent(active.index_percent)}</span>
        </div>
      ) : null}
      <p className="sr-only" aria-live="polite">
        {hoverIndex != null && active
          ? `${active.time ?? active.date ?? "当前点"}，组合${formatPercent(active.portfolio_percent)}，上证${formatPercent(active.index_percent)}`
          : ""}
      </p>
    </div>
  );
}
