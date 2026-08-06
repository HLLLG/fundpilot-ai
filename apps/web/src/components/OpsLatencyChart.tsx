"use client";

import { memo, useMemo } from "react";
import type { OpsTrafficPoint } from "@/lib/api/ops";

/**
 * 响应时间趋势（平均值 + P95）。
 *
 * 两条线一起看才有意义：平均值平稳而 P95 抬高，说明只有部分请求变慢（通常是某个
 * 慢接口或缓存未命中）；两条同时抬高才是整体性退化。
 */

type OpsLatencyChartProps = {
  points: OpsTrafficPoint[];
  height?: number;
};

const VIEW_WIDTH = 1000;
const AXIS_LEFT = 8;
const AXIS_RIGHT = 8;

function niceCeiling(value: number): number {
  if (value <= 0) {
    return 1;
  }
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return nice * magnitude;
}

function formatMs(value: number): string {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}s`;
  }
  return `${Math.round(value)}ms`;
}

/** 缺测点断开折线，而不是插值连过去——插值会凭空造出没测到的数据。 */
function buildSegments(
  values: Array<number | null>,
  x: (index: number) => number,
  y: (value: number) => number,
): string[] {
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length > 1) {
        segments.push(current.join(" "));
      }
      current = [];
      return;
    }
    current.push(`${x(index).toFixed(2)},${y(value).toFixed(2)}`);
  });
  if (current.length > 1) {
    segments.push(current.join(" "));
  }
  return segments;
}

export const OpsLatencyChart = memo(function OpsLatencyChart({
  points,
  height = 150,
}: OpsLatencyChartProps) {
  const model = useMemo(() => {
    const plotWidth = VIEW_WIDTH - AXIS_LEFT - AXIS_RIGHT;
    const observed = points.flatMap((point) =>
      [point.mean_ms, point.p95_ms].filter((value): value is number => value !== null),
    );
    const maxMs = niceCeiling(Math.max(1, ...observed));
    const step = points.length > 1 ? plotWidth / (points.length - 1) : plotWidth;
    const x = (index: number) =>
      points.length > 1 ? AXIS_LEFT + index * step : AXIS_LEFT + plotWidth / 2;
    const y = (value: number) => height - (value / maxMs) * (height - 12);
    return {
      maxMs,
      hasData: observed.length > 0,
      meanSegments: buildSegments(points.map((point) => point.mean_ms), x, y),
      p95Segments: buildSegments(points.map((point) => point.p95_ms), x, y),
    };
  }, [height, points]);

  if (!model.hasData) {
    return (
      <p className="py-10 text-center text-sm text-slate-500">该时间窗口内没有响应时间数据</p>
    );
  }

  return (
    <figure className="m-0">
      <figcaption className="sr-only">
        {`响应时间趋势，纵轴上限 ${formatMs(model.maxMs)}`}
      </figcaption>
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`响应时间趋势图，最高约 ${formatMs(model.maxMs)}`}
      >
        {[0.25, 0.5, 0.75].map((ratio) => (
          <line
            key={ratio}
            x1={AXIS_LEFT}
            x2={VIEW_WIDTH - AXIS_RIGHT}
            y1={height * ratio}
            y2={height * ratio}
            stroke="rgb(226,232,240)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {model.p95Segments.map((segment, index) => (
          <polyline
            key={`p95-${index}`}
            points={segment}
            fill="none"
            stroke="rgb(234,88,12)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {model.meanSegments.map((segment, index) => (
          <polyline
            key={`mean-${index}`}
            points={segment}
            fill="none"
            stroke="rgb(15,118,110)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500">
        <span>0</span>
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            <span aria-hidden="true" className="h-0.5 w-3 bg-[rgb(15,118,110)]" />
            平均
          </span>
          <span className="flex items-center gap-1">
            <span aria-hidden="true" className="h-0.5 w-3 bg-[rgb(234,88,12)]" />
            P95
          </span>
        </span>
        <span>{formatMs(model.maxMs)}</span>
      </div>
    </figure>
  );
});
