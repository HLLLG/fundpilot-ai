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
  //: 收益类分量里是否有一条"可靠性放行"的。`reliability.usable` 由后端
  //: `signal_synthesis._reliability_block` 写入（可靠性 ∈ {高, 中}）。
  //:
  //: 这一路不可用时，「正向支持 不足」说的是**这类基金的因子统计不可用**，不是
  //: "这只基金量化表现差"——因子可靠性是同类组共用的属性（`reliability.scope`），
  //: 同一同类组内每只基金逐字相同。不加这句区分，用户会把一个全体恒等的常量读成
  //: 对自己这只基金的评价。
  const hasUsableReturnEvidence = (evidence.components ?? []).some(
    (component) =>
      component?.role === "return_signal" && component?.reliability?.usable === true,
  );
  const reliabilityScope = (evidence.components ?? []).find(
    (component) => component?.role === "return_signal",
  )?.reliability?.scope;

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
        {/* 覆盖 = 特征字段齐全度，不是统计样本量。标注出来，避免它摆在「可靠性」旁边时
            被读成统计可信度的一部分。 */}
        {coverage != null ? (
          <Metric label="特征齐全度" value={`${coverage.toFixed(0)}%`} warning={coverage < 50} />
        ) : null}
        <Metric
          label="时效"
          value={FRESHNESS_LABEL[freshness] ?? freshness}
          warning={freshness !== "fresh"}
        />
        {riskGuards > 0 ? <Metric label="风险守卫" value={`${riskGuards} 路`} warning /> : null}
      </div>
      {!hasUsableReturnEvidence ? (
        <p
          data-testid="quant-evidence-scope-note"
          className="break-words text-[11px] leading-5 text-[var(--warn-fg)] [overflow-wrap:anywhere]"
        >
          {reliabilityScope === "peer_group"
            ? "本次因子 IC 在该基金所属同类组未达到可用标准，这一路证据不参与结论。该可靠性是同类基金共用的统计属性，不代表这只基金自身表现差。"
            : "本次没有一路收益证据通过可靠性门槛，量化证据不参与结论；这不等于基金自身表现差。"}
        </p>
      ) : null}
      {!compact ? (
        <p className="break-words text-xs leading-5 text-slate-500 [overflow-wrap:anywhere]">
          {evidence.summary}
        </p>
      ) : null}
    </div>
  );
}
