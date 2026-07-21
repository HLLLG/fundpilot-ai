"use client";

import { useMemo } from "react";
import type { BoardFlowHistoryPoint } from "@/lib/api";
import { formatThemeFlowYi, profitToneClass } from "@/lib/marketThemeBoard";

type BoardFlowHistoryChartProps = {
  points: BoardFlowHistoryPoint[];
  cumulativeNetYi?: number | null;
  height?: number;
};

type BoardFlowAxisDomain = {
  min: number;
  max: number;
  ticks: number[];
};

const AXIS_FONT_SIZE = 9;
const DATE_FONT_SIZE = 9;
const PLOT_LEFT_PX = 36;
const PLOT_RIGHT_PX = 8;

function finiteFlowValue(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function mapBoardFlowHistoryValues(points: BoardFlowHistoryPoint[]) {
  return points.map((point) => finiteFlowValue(point.main_force_net_yi));
}

export function formatBoardFlowAxisYi(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  if (Math.abs(rounded) < 0.05) {
    return "0";
  }
  const absolute = Math.abs(rounded);
  if (absolute >= 1000) {
    const compact = (absolute / 1000).toFixed(absolute >= 10_000 ? 0 : 1).replace(/\.0$/, "");
    return `${rounded > 0 ? "+" : "-"}${compact}k`;
  }
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(1)}`;
}

function niceOuterBound(value: number): number {
  const positive = Math.max(Math.abs(value), 0.01);
  const magnitude = 10 ** Math.floor(Math.log10(positive));
  const normalized = positive / magnitude;
  const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  return nice * magnitude;
}

export function buildBoardFlowAxisDomain(values: number[]): BoardFlowAxisDomain {
  const finiteValues = values.filter(Number.isFinite);
  if (finiteValues.length === 0 || finiteValues.every((value) => value === 0)) {
    return { min: -1, max: 1, ticks: [0] };
  }

  const rawMin = Math.min(0, ...finiteValues);
  const rawMax = Math.max(0, ...finiteValues);
  const min = rawMin < 0 ? -niceOuterBound(Math.abs(rawMin) * 1.08) : 0;
  const max = rawMax > 0 ? niceOuterBound(rawMax * 1.08) : 0;

  if (min === 0) {
    return { min, max, ticks: [0, max / 2, max] };
  }
  if (max === 0) {
    return { min, max, ticks: [min, min / 2, 0] };
  }
  return { min, max, ticks: [min, 0, max] };
}

function formatDateLabel(date: string): string {
  const parts = date.split("-");
  if (parts.length >= 3) {
    return `${parts[1]}-${parts[2]}`;
  }
  return date;
}

/** 控制 x 轴日期标签密度，近一月只保留关键锚点。 */
function pickDateLabelFlags(count: number, maxLabels = 5): boolean[] {
  if (count <= 0) {
    return [];
  }
  if (count <= maxLabels) {
    return Array.from({ length: count }, () => true);
  }

  const flags = Array.from({ length: count }, () => false);
  flags[0] = true;
  flags[count - 1] = true;
  const innerSlots = maxLabels - 2;
  const step = (count - 1) / (innerSlots + 1);
  for (let slot = 1; slot <= innerSlots; slot += 1) {
    flags[Math.round(step * slot)] = true;
  }
  return flags;
}

function barWidthForCount(count: number): number {
  if (count <= 7) {
    return 14;
  }
  if (count <= 12) {
    return 10;
  }
  return 7;
}

export function BoardFlowHistoryChart({
  points,
  cumulativeNetYi,
  height = 124,
}: BoardFlowHistoryChartProps) {
  const chart = useMemo(() => {
    if (points.length === 0) {
      return null;
    }

    const mappedValues = mapBoardFlowHistoryValues(points);
    const values = mappedValues.filter((value): value is number => value != null);
    if (values.length === 0) {
      return null;
    }

    const domain = buildBoardFlowAxisDomain(values);
    const paddingTop = 7;
    const paddingBottom = 20;
    const plotHeight = height - paddingTop - paddingBottom;
    const domainSpan = domain.max - domain.min || 1;
    const zeroY = paddingTop + ((domain.max - 0) / domainSpan) * plotHeight;
    const labelFlags = pickDateLabelFlags(points.length);
    const barWidth = barWidthForCount(points.length);

    const yForValue = (value: number) =>
      paddingTop + ((domain.max - value) / domainSpan) * plotHeight;

    const bars = points.map((point, index) => {
      const value = mappedValues[index];
      const xPercent = ((index + 0.5) / points.length) * 100;
      const common = {
        key: `${point.date}-${index}`,
        value,
        date: point.date,
        xPercent,
        showLabel: labelFlags[index] ?? false,
      };
      if (value == null) {
        return { ...common, rect: null };
      }
      const yValue = yForValue(value);
      const yTop = Math.min(zeroY, yValue);
      const rectHeight = Math.max(Math.abs(yValue - zeroY), value === 0 ? 1 : 2);
      return {
        ...common,
        rect: {
          y: yTop,
          width: barWidth,
          height: rectHeight,
          fill: value > 0 ? "#c83e55" : value < 0 ? "#198568" : "#94a3b8",
        },
      };
    });

    return {
      height,
      zeroY,
      bars,
      yTicks: domain.ticks,
      yForValue,
    };
  }, [height, points]);

  if (!chart) {
    return <p className="py-6 text-center text-xs text-slate-500">暂无历史资金流数据</p>;
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200/80 bg-white/70">
      <div className="flex min-h-10 items-center justify-between gap-3 border-b border-slate-100 px-3 py-2">
        {cumulativeNetYi != null ? (
          <div className="flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 text-[10px] font-medium text-slate-500">区间累计</span>
            <span
              className={`truncate text-sm font-bold tabular-nums ${profitToneClass(cumulativeNetYi)}`}
            >
              {formatThemeFlowYi(cumulativeNetYi)}
            </span>
          </div>
        ) : <span />}
        <span className="shrink-0 text-[9px] tracking-wide text-slate-400">单日净额 · 亿元</span>
      </div>

      <svg
        width="100%"
        height={chart.height}
        className="block w-full"
        role="img"
        aria-label="板块主力净流入历史柱状图，单位亿元"
      >
        {chart.yTicks.map((tick) => {
          const y = chart.yForValue(tick);
          return (
            <g key={tick}>
              <text
                x={PLOT_LEFT_PX - 5}
                y={y + 3}
                textAnchor="end"
                className="fill-slate-400 tabular-nums"
                fontSize={AXIS_FONT_SIZE}
              >
                {formatBoardFlowAxisYi(tick)}
              </text>
            </g>
          );
        })}

        <svg
          x={PLOT_LEFT_PX}
          y={0}
          width={`calc(100% - ${PLOT_LEFT_PX + PLOT_RIGHT_PX}px)`}
          height={chart.height}
        >
          {chart.yTicks.map((tick) => {
            const y = chart.yForValue(tick);
            return (
              <line
                key={tick}
                x1="0%"
                x2="100%"
                y1={y}
                y2={y}
                stroke={tick === 0 ? "rgba(100,116,139,0.34)" : "rgba(148,163,184,0.16)"}
                strokeWidth={tick === 0 ? 1 : 0.75}
                strokeDasharray={tick === 0 ? undefined : "2 4"}
              />
            );
          })}

          {chart.bars.map((bar) => (
            <g key={bar.key}>
              {bar.rect && bar.value != null ? (
                <rect
                  x={`${bar.xPercent}%`}
                  y={bar.rect.y}
                  width={bar.rect.width}
                  height={bar.rect.height}
                  rx={2}
                  fill={bar.rect.fill}
                  opacity={0.9}
                  transform={`translate(${-bar.rect.width / 2} 0)`}
                >
                  <title>
                    {bar.date} 主力 {formatThemeFlowYi(bar.value)}
                  </title>
                </rect>
              ) : null}
              {bar.showLabel ? (
                <text
                  x={`${bar.xPercent}%`}
                  y={chart.height - 6}
                  textAnchor="middle"
                  className="fill-slate-400 tabular-nums"
                  fontSize={DATE_FONT_SIZE}
                >
                  {formatDateLabel(bar.date)}
                </text>
              ) : null}
            </g>
          ))}
        </svg>
      </svg>
    </div>
  );
}
