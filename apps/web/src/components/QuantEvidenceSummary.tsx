import type { HoldingEvidence } from "@/lib/api";

const DIRECTION_LABEL: Record<string, string> = {
  positive: "正向",
  negative: "负向",
  mixed: "方向分歧",
  neutral: "中性",
  unknown: "方向不足",
};

const FRESHNESS_LABEL: Record<string, string> = {
  fresh: "新鲜",
  stale: "已过期",
  unavailable: "不可用",
  unknown: "时点未知",
};

function Metric({ label, value, warning = false }: { label: string; value: string; warning?: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] leading-none ${
        warning
          ? "border-[var(--warn-border)] bg-[var(--warn-bg)] text-[var(--warn-fg)]"
          : "border-slate-200 bg-white text-slate-600"
      }`}
    >
      <span className="text-slate-400">{label}</span>
      <span className="font-semibold">{value}</span>
    </span>
  );
}

export function QuantEvidenceSummary({ evidence, compact = false }: { evidence: HoldingEvidence; compact?: boolean }) {
  const composite = evidence.composite;
  const reliability = composite.reliability?.level ?? composite.level;
  const direction = composite.direction ?? "unknown";
  const coverage = composite.coverage?.percent;
  const freshness = composite.freshness?.status ?? "unknown";
  const riskGuards = composite.risk_guard_count ?? evidence.risk_guards?.length ?? 0;
  const isV2 = evidence.schema_version === "quant_evidence.v2";

  if (!isV2) {
    return (
      <p className="break-words text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]">
        量化证据（旧口径）：{evidence.summary}
      </p>
    );
  }

  return (
    <div className={compact ? "space-y-1.5" : "space-y-2"} data-testid="quant-evidence-summary">
      <div className="flex flex-wrap gap-1.5">
        <Metric label="正向支持" value={composite.level} warning={composite.level === "不足" || composite.level === "低"} />
        <Metric label="可靠性" value={reliability} warning={reliability === "不足" || reliability === "低"} />
        <Metric
          label="方向"
          value={DIRECTION_LABEL[direction] ?? direction}
          warning={direction === "negative" || direction === "mixed" || direction === "unknown"}
        />
        {coverage != null ? <Metric label="覆盖" value={`${coverage.toFixed(0)}%`} warning={coverage < 50} /> : null}
        <Metric
          label="时效"
          value={FRESHNESS_LABEL[freshness] ?? freshness}
          warning={freshness !== "fresh"}
        />
        {riskGuards > 0 ? <Metric label="风险守卫" value={`${riskGuards} 路`} warning /> : null}
      </div>
      {!compact ? (
        <p className="break-words text-xs leading-5 text-slate-500 [overflow-wrap:anywhere]">
          {evidence.summary}
        </p>
      ) : null}
    </div>
  );
}
