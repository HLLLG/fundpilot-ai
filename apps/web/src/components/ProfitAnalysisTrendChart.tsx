"use client";

import { useId, useMemo, useState } from "react";
import type { ProfitTrend } from "@/lib/api";
import { clockToSessionRatio } from "@/lib/intradayChartTime";

const INDEX_COLOR = "#5B8DEF";
const AXIS_FONT_SIZE = 10;
const AXIS_LABEL_CLASS = "fill-slate-400 tabular-nums";

type ProfitAnalysisTrendChartProps = {
  trend: ProfitTrend | null | undefined;
  height?: number;
};

function formatPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

const Y_AXIS_STEP = 0.75;

function formatAxisLabel(value: number) {
  const rounded = Math.round(value * 100) / 100;
  if (Math.abs(rounded) < 0.005) {
    return "0.00%";
  }
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

/** 按组合收益 + 上证曲线极值非对称定轴，避免指数线被裁切。 */
function computeAxisBounds(values: number[], step: number = Y_AXIS_STEP) {
  const safeValues = values.length ? values : [0];
  const dataMin = Math.min(...safeValues, 0);
  const dataMax = Math.max(...safeValues, 0);

  const downExtent = Math.abs(Math.min(dataMin, 0));
  const upExtent = Math.max(dataMax, 0);
  const paddedDown = downExtent > 0 ? downExtent * 1.12 + step * 0.2 : step;
  const paddedUp = upExtent > 0 ? upExtent * 1.12 + step * 0.2 : step;

  const stepsBelow = Math.max(1, Math.ceil(paddedDown / step));
  const stepsAbove = Math.max(1, Math.ceil(paddedUp / step));

  return {
    min: -stepsBelow * step,
    max: stepsAbove * step,
  };
}

function buildYTicks(min: number, max: number, step: number = Y_AXIS_STEP) {
  const ticks: number[] = [];
  const count = Math.round((max - min) / step);
  for (let index = 0; index <= count; index += 1) {
    ticks.push(Math.round((min + index * step) * 100) / 100);
  }
  return ticks;
}

function leftPaddingForLabels(maxAbs: number) {
  const sample = formatAxisLabel(maxAbs);
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

  const chart = useMemo(() => {
    const points = trend?.points ?? [];
    if (points.length < 2) {
      return null;
    }

    const portfolioValues = points.map((point) => point.portfolio_percent ?? 0);
    const indexValues = points
      .map((point) => point.index_percent)
      .filter((value): value is number => value != null && !Number.isNaN(value));
    const axisValues = [...portfolioValues, ...indexValues];
    const { min, max } = computeAxisBounds(axisValues);
    const yTickValues = buildYTicks(min, max);
    const leftPad = leftPaddingForLabels(Math.max(Math.abs(min), Math.abs(max)));
    const padding = { top: 12, right: 10, bottom: 14, left: leftPad };
    const width = 360 + (leftPad - 46);
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const range = max - min || 1;
    const plotTop = padding.top;
    const plotBottom = padding.top + chartHeight;
    const plotLeft = padding.left;
    const plotRight = padding.left + chartWidth;

    const toY = (percent: number) => plotBottom - ((percent - min) / range) * chartHeight;

    const coords = points.map((point, index) => {
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
        portfolioY: toY(point.portfolio_percent ?? 0),
        indexY: point.index_percent != null ? toY(point.index_percent) : null,
        index,
      };
    });

    const portfolioPath = coords
      .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.portfolioY}`)
      .join(" ");
    const portfolioArea = `${portfolioPath} L ${coords[coords.length - 1].x} ${plotBottom} L ${coords[0].x} ${plotBottom} Z`;
    const indexPath =
      coords.filter((point) => point.indexY != null).length >= 2
        ? coords
            .filter((point) => point.indexY != null)
            .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.indexY}`)
            .join(" ")
        : null;

    const baselineY = toY(0);
    const yTicks = yTickValues.map((value) => ({
      value,
      y: toY(value),
      label: formatAxisLabel(value),
      isZero: Math.abs(value) < Y_AXIS_STEP / 2,
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
    };
  }, [height, trend]);

  if (!chart) {
    return (
      <div
        className="flex items-center justify-center rounded-xl bg-slate-50 text-sm text-slate-400"
        style={{ height }}
      >
        暂无走势数据
      </div>
    );
  }

  const active = hoverIndex != null ? chart.coords[hoverIndex] : chart.coords[chart.coords.length - 1];

  return (
    <div className="relative w-full select-none">
      <svg
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        className="w-full overflow-visible"
        role="img"
        aria-label="收益走势"
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

        <path d={chart.portfolioArea} fill={`url(#${gradientId})`} clipPath={`url(#${gradientId}-plot)`} />
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
        <path
          d={chart.portfolioPath}
          fill="none"
          stroke={chart.colors.line}
          strokeWidth={1.1}
          strokeLinecap="round"
          strokeLinejoin="round"
          clipPath={`url(#${gradientId}-plot)`}
        />

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
            <circle cx={active.x} cy={active.portfolioY} r="2.5" fill="#fff" stroke={chart.colors.line} strokeWidth={1.1} />
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
          <span className="mx-1.5 text-slate-300">·</span>
          <span style={{ color: INDEX_COLOR }}>上证 {formatPercent(active.index_percent)}</span>
        </div>
      ) : null}
    </div>
  );
}
