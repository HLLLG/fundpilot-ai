"use client";

import { useId, useMemo } from "react";
import type { BoardFlowHistoryPoint } from "@/lib/api";
import { formatThemeFlowYi, profitToneClass } from "@/lib/marketThemeBoard";

type BoardFlowHistoryChartProps = {
  points: BoardFlowHistoryPoint[];
  cumulativeNetYi?: number | null;
  height?: number;
};

function formatAxisYi(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  if (Math.abs(rounded) < 0.005) {
    return "0";
  }
  return `${Math.abs(rounded).toFixed(1)}`;
}

function formatDateLabel(date: string): string {
  const parts = date.split("-");
  if (parts.length >= 3) {
    return `${parts[1]}-${parts[2]}`;
  }
  return date;
}

/** 控制 x 轴日期标签密度，避免近一月 20 个点挤在一起重叠。 */
function pickDateLabelFlags(count: number, maxLabels = 6): boolean[] {
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

export function BoardFlowHistoryChart({
  points,
  cumulativeNetYi,
  height = 160,
}: BoardFlowHistoryChartProps) {
  const gradientId = useId().replace(/:/g, "");

  const chart = useMemo(() => {
    if (points.length === 0) {
      return null;
    }

    const values = points.map((point) => point.main_force_net_yi ?? 0);
    const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 0.01);
    const axisMax = Math.ceil(maxAbs * 1.15 * 10) / 10;
    const width = 320;
    const paddingLeft = 36;
    const paddingRight = 8;
    const paddingTop = 8;
    const paddingBottom = 22;
    const plotWidth = width - paddingLeft - paddingRight;
    const plotHeight = height - paddingTop - paddingBottom;
    const zeroY = paddingTop + plotHeight / 2;
    const barSlot = plotWidth / points.length;
    const barWidth = Math.max(8, Math.min(22, barSlot * 0.62));
    const labelFlags = pickDateLabelFlags(points.length);

    const yForValue = (value: number) => {
      const ratio = value / axisMax;
      return zeroY - ratio * (plotHeight / 2);
    };

    const bars = points.map((point, index) => {
      const value = point.main_force_net_yi ?? 0;
      const xCenter = paddingLeft + barSlot * index + barSlot / 2;
      const yValue = yForValue(value);
      const yTop = Math.min(zeroY, yValue);
      const barHeight = Math.max(Math.abs(yValue - zeroY), value === 0 ? 1 : 2);
      const fill = value >= 0 ? "#e11d48" : "#059669";
      return {
        key: `${point.date}-${index}`,
        x: xCenter - barWidth / 2,
        y: yTop,
        width: barWidth,
        height: barHeight,
        fill,
        value,
        date: point.date,
        labelX: xCenter,
        showLabel: labelFlags[index] ?? false,
      };
    });

    const yTicks = [-axisMax, -axisMax / 2, 0, axisMax / 2, axisMax];

    return {
      width,
      height,
      paddingLeft,
      paddingTop,
      plotHeight,
      zeroY,
      axisMax,
      bars,
      yTicks,
      yForValue,
    };
  }, [height, points]);

  if (!chart) {
    return <p className="py-6 text-center text-xs text-slate-400">暂无历史资金流数据</p>;
  }

  return (
    <div className="space-y-2">
      {cumulativeNetYi != null ? (
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-slate-400">区间累计主力净流入</span>
          <span className={`tabular-nums font-semibold ${profitToneClass(cumulativeNetYi)}`}>
            {formatThemeFlowYi(cumulativeNetYi)}
          </span>
        </div>
      ) : null}

      <svg
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        className="w-full max-w-full"
        role="img"
        aria-label="板块主力净流入历史柱状图"
      >
        <defs>
          <linearGradient id={`${gradientId}-zero`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(148,163,184,0.08)" />
            <stop offset="100%" stopColor="rgba(148,163,184,0.02)" />
          </linearGradient>
        </defs>

        {chart.yTicks.map((tick) => {
          const y = chart.yForValue(tick);
          return (
            <g key={tick}>
              <line
                x1={chart.paddingLeft}
                x2={chart.width - 8}
                y1={y}
                y2={y}
                stroke={tick === 0 ? "rgba(148,163,184,0.45)" : "rgba(148,163,184,0.12)"}
                strokeWidth={tick === 0 ? 1 : 0.75}
                strokeDasharray={tick === 0 ? undefined : "2 3"}
              />
              <text
                x={chart.paddingLeft - 4}
                y={y + 3}
                textAnchor="end"
                className="fill-slate-400 text-[5px] tabular-nums"
              >
                {formatAxisYi(tick)}
              </text>
            </g>
          );
        })}

        {chart.bars.map((bar) => (
          <g key={bar.key}>
            <rect
              x={bar.x}
              y={bar.y}
              width={bar.width}
              height={bar.height}
              rx={2}
              fill={bar.fill}
              opacity={0.92}
            >
              <title>
                {bar.date} 主力 {formatThemeFlowYi(bar.value)}
              </title>
            </rect>
            {bar.showLabel ? (
              <text
                x={bar.labelX}
                y={chart.height - 6}
                textAnchor="middle"
                className="fill-slate-400 text-[5px] tabular-nums"
              >
                {formatDateLabel(bar.date)}
              </text>
            ) : null}
          </g>
        ))}
      </svg>
    </div>
  );
}
