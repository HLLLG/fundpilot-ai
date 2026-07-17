import { RefreshCw } from "lucide-react";

import type {
  DiscoveryEntryTrigger,
  DiscoveryEntryTriggerCondition,
} from "@/lib/api";

const OPERATOR_LABEL: Record<string, string> = {
  lt: "<",
  lte: "≤",
  gt: ">",
  gte: "≥",
  eq: "=",
};

function formatMetricValue(value: number, unit = ""): string {
  const text = new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: Number.isInteger(value) ? 0 : 1,
    signDisplay: unit === "%" || unit === "亿元" ? "exceptZero" : "auto",
  }).format(value);
  return `${text}${unit}`;
}

function currentLabel(condition: DiscoveryEntryTriggerCondition): string {
  if (condition.current_text) return condition.current_text;
  return typeof condition.current_value === "number"
    ? formatMetricValue(condition.current_value, condition.unit)
    : "待更新";
}

function targetLabel(condition: DiscoveryEntryTriggerCondition): string {
  if (condition.target_text) return condition.target_text;
  if (typeof condition.target_value !== "number") return "等待改善";
  const operator = condition.operator ? OPERATOR_LABEL[condition.operator] ?? "" : "";
  return `${operator}${formatMetricValue(condition.target_value, condition.unit)}`;
}

export function DiscoveryEntryTriggerCard({
  trigger,
  compact = false,
}: {
  trigger?: DiscoveryEntryTrigger | null;
  compact?: boolean;
}) {
  const conditions = trigger?.conditions?.filter((item) => item.label) ?? [];

  if (!conditions.length) {
    return (
      <div
        aria-label="等待条件未记录"
        className="mt-3 rounded-xl border border-amber-200 bg-white/75 px-3 py-2 text-xs text-amber-950"
      >
        <span className="font-black">等待条件未记录</span>
        <span className="ml-2 text-amber-800">重新生成荐基后补齐具体触发值</span>
      </div>
    );
  }

  const visibleConditions = conditions.slice(0, compact ? 2 : 3);
  return (
    <section
      aria-label="具体等待条件"
      className={`mt-3 rounded-xl border border-amber-200 bg-white/80 ${compact ? "px-3 py-2.5" : "p-3"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-black text-amber-950">
            {trigger?.headline || "等待条件改善"}
          </p>
          <p className="mt-0.5 text-[11px] leading-5 text-amber-800">
            当前不买入，目标出现后重新评估
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-bold text-amber-700">
          <RefreshCw size={12} aria-hidden="true" />
          自动复核
        </span>
      </div>

      <dl className={`mt-2 grid gap-1.5 ${visibleConditions.length > 1 ? "sm:grid-cols-2" : ""}`}>
        {visibleConditions.map((condition) => (
          <div
            key={`${condition.metric}-${condition.label}`}
            className="flex min-w-0 items-center justify-between gap-2 rounded-lg bg-amber-50/80 px-2.5 py-2"
          >
            <dt className="truncate text-[11px] font-bold text-slate-600">{condition.label}</dt>
            <dd className="shrink-0 font-mono text-[11px] font-black tabular-nums text-slate-950">
              {currentLabel(condition)}
              <span className="mx-1 text-amber-500">→</span>
              <span className="text-amber-900">{targetLabel(condition)}</span>
            </dd>
          </div>
        ))}
      </dl>

      <p className="mt-2 text-[10px] leading-4 text-slate-500">
        {conditions.length > visibleConditions.length
          ? `另有 ${conditions.length - visibleConditions.length} 项 · `
          : ""}
        {trigger?.release_mode === "all" ? "全部改善后" : "任一改善后"}进入复核；
        {trigger?.recheck_label || "下次荐基扫描自动复核"}，不自动下单。
      </p>
    </section>
  );
}
