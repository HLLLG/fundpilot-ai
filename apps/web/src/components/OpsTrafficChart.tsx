"use client";

import { memo, useId, useMemo } from "react";
import type { OpsTrafficPoint } from "@/lib/api/ops";

/**
 * 流量与错误趋势图（手写 SVG，项目不引入图表库）。
 *
 * 两条信息叠在同一时间轴上：面积是请求量，红色柱是服务端错误数。这样"错误是否
 * 集中在某个流量高峰"一眼可见，而这正是判断"是并发问题还是代码问题"的第一步。
 */

type OpsTrafficChartProps = {
  points: OpsTrafficPoint[];
  bucketSeconds: number;
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

function formatBucketLabel(iso: string, bucketSeconds: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  const time = date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (bucketSeconds >= 3600) {
    const day = date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
    return `${day} ${time}`;
  }
  return time;
}

export const OpsTrafficChart = memo(function OpsTrafficChart({
  points,
  bucketSeconds,
  height = 180,
}: OpsTrafficChartProps) {
  const gradientId = useId();

  const model = useMemo(() => {
    const plotWidth = VIEW_WIDTH - AXIS_LEFT - AXIS_RIGHT;
    const maxRequests = niceCeiling(
      Math.max(1, ...points.map((point) => point.request_count)),
    );
    const maxErrors = Math.max(1, ...points.map((point) => point.server_error_count));
    const step = points.length > 1 ? plotWidth / (points.length - 1) : plotWidth;
    const barWidth = Math.max(
      1.5,
      Math.min(10, points.length > 0 ? (plotWidth / points.length) * 0.6 : 6),
    );

    const x = (index: number) =>
      points.length > 1 ? AXIS_LEFT + index * step : AXIS_LEFT + plotWidth / 2;
    const requestY = (value: number) => height - (value / maxRequests) * (height - 12);

    const linePoints = points
      .map((point, index) => `${x(index).toFixed(2)},${requestY(point.request_count).toFixed(2)}`)
      .join(" ");
    const areaPath = points.length
      ? `M ${AXIS_LEFT},${height} L ${linePoints.split(" ").join(" L ")} L ${(
          points.length > 1 ? AXIS_LEFT + plotWidth : AXIS_LEFT + plotWidth / 2
        ).toFixed(2)},${height} Z`
      : "";

    const bars = points
      .map((point, index) => ({
        key: point.bucket_start,
        x: x(index) - barWidth / 2,
        // 错误柱用独立标尺：错误数通常比请求数小两个数量级，
        // 共用标尺会让它永远贴着底线看不见。
        y: height - Math.max(2, (point.server_error_count / maxErrors) * (height * 0.55)),
        width: barWidth,
        count: point.server_error_count,
      }))
      .filter((bar) => bar.count > 0);

    return { maxRequests, maxErrors, linePoints, areaPath, bars, x, requestY };
  }, [height, points]);

  if (points.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-slate-500">该时间窗口内没有流量数据</p>
    );
  }

  const firstLabel = formatBucketLabel(points[0].bucket_start, bucketSeconds);
  const lastLabel = formatBucketLabel(
    points[points.length - 1].bucket_start,
    bucketSeconds,
  );
  const totalRequests = points.reduce((sum, point) => sum + point.request_count, 0);
  const totalErrors = points.reduce((sum, point) => sum + point.server_error_count, 0);

  return (
    <figure className="m-0">
      <figcaption className="sr-only">
        {`流量与错误趋势：${firstLabel} 至 ${lastLabel}，共 ${totalRequests} 次请求、${totalErrors} 次服务端错误`}
      </figcaption>
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`流量与错误趋势图，峰值 ${model.maxRequests} 次请求/桶`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(35,86,224)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="rgb(35,86,224)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

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

        <path d={model.areaPath} fill={`url(#${gradientId})`} />
        <polyline
          points={model.linePoints}
          fill="none"
          stroke="rgb(35,86,224)"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
        {model.bars.map((bar) => (
          <rect
            key={bar.key}
            x={bar.x}
            y={bar.y}
            width={bar.width}
            height={height - bar.y}
            fill="rgb(220,38,38)"
            opacity="0.85"
            rx="1"
          />
        ))}
      </svg>
      <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500">
        <span>{firstLabel}</span>
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            <span aria-hidden="true" className="h-2 w-3 rounded-sm bg-[rgb(35,86,224)]/40" />
            请求数（峰值 {model.maxRequests}）
          </span>
          <span className="flex items-center gap-1">
            <span aria-hidden="true" className="h-2 w-2 rounded-sm bg-red-600" />
            服务端错误
          </span>
        </span>
        <span>{lastLabel}</span>
      </div>
    </figure>
  );
});
