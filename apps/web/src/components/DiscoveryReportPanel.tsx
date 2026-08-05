"use client";

import { useMemo, useState } from "react";
import {
  BarChart3,
  BookOpenCheck,
  ChevronDown,
  CircleDollarSign,
  MessageCircle,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import type {
  DiscoveryCandidatePoolItem,
  DiscoveryRecommendation,
  FundDiscoveryReport,
} from "@/lib/api";
import { actionBadgeClass } from "@/lib/actionStyles";
import { translateEvidenceText } from "@/lib/decisionText";
import { DecisionEvidenceGrid } from "@/components/DecisionEvidenceGrid";
import {
  DiscoveryCandidatePoolPanel,
  type DiscoveryCandidateDecisionStatus,
} from "@/components/DiscoveryCandidatePoolPanel";
import { DiscoveryChatDrawer } from "@/components/DiscoveryChatDrawer";
import { DiscoveryOutcomesPanel } from "@/components/DiscoveryOutcomesPanel";
import {
  SectorOpportunityCard,
  isEntryMaturityPolicy,
} from "@/components/SectorOpportunityCard";

function DiscoveryPositionChangeBadge({
  percent,
  basis,
}: {
  percent: number;
  basis?: string | null;
}) {
  const isBoost = percent > 0;
  const Icon = isBoost ? TrendingUp : TrendingDown;
  const toneClass = isBoost
    ? "border-emerald-200 bg-emerald-50 text-emerald-900"
    : "border-rose-200 bg-rose-50 text-rose-900";
  return (
    <div className={`mt-2 flex items-start gap-2 rounded-xl border px-3 py-2 ${toneClass}`}>
      <Icon size={18} className="mt-0.5 flex-shrink-0" />
      <div className="min-w-0">
        <div className="text-sm font-black">
          {isBoost ? "建议提高金额上限" : "建议降低配置"} {Math.abs(percent).toFixed(0)}%
        </div>
        {basis ? (
          <p className="mt-0.5 break-words text-xs leading-5 opacity-80 [overflow-wrap:anywhere]">
            {translateEvidenceText(basis)}
          </p>
        ) : null}
      </div>
    </div>
  );
}

type DiscoveryReportPanelProps = {
  report: FundDiscoveryReport;
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
};

const EXECUTABLE_DISCOVERY_ACTIONS = new Set(["分批买入", "建议买入", "买入", "申购"]);
const CURRENT_DISCOVERY_AMOUNT_SEMANTICS = new Set([
  "current_verified_opportunity_amount",
  "advisory_current_opportunity_amount",
  // Historical reports retain the previous internal vocabulary.
  "current_verified_initial_tranche",
  "advisory_initial_tranche",
]);

function finiteAmount(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

// formatter 提到模块作用域：荐基报告按推荐条数逐条渲染金额。无选项的
// `Intl.NumberFormat(locale)` 与 `n.toLocaleString(locale)` 输出一致。
const YUAN_FORMATTER = new Intl.NumberFormat("zh-CN");

function formatYuan(value: number | null | undefined, fallback = "未确认"): string {
  const amount = finiteAmount(value);
  return amount == null ? fallback : `¥${YUAN_FORMATTER.format(amount)}`;
}

function isObsoleteDiscoveryCashCaveat(value: string): boolean {
  return value.includes("现金未单独录入")
    || value.includes("现金未单录")
    || value.includes("本次扫描可用现金");
}

export function discoveryActionDisplayLabel(
  recommendation: DiscoveryRecommendation,
): string {
  if (recommendation.action === "分批买入") return "建议买入";
  if (recommendation.action !== "等待回调") return recommendation.action;
  switch (recommendation.waiting_reason_code) {
    case "flow_confirmation":
      return "等待资金确认";
    case "fund_entry_confirmation":
      return "等待基金信号";
    case "structure_repair":
      return "等待结构修复";
    case "trend_confirmation":
      return "等待趋势确认";
    case "data_quality":
      return "等待数据确认";
    case "trend_or_structure_invalid":
      return "等待重新转强";
    default:
      return recommendation.action;
  }
}

function isCurrentDiscoveryAllocation(recommendation: DiscoveryRecommendation): boolean {
  const allocation = recommendation.allocation;
  const recommendationAmount = finiteAmount(recommendation.suggested_amount_yuan);
  const allocationAmount = finiteAmount(
    allocation?.suggested_amount_yuan,
  );
  const allocationCode = allocation?.fund_code?.trim().padStart(6, "0");
  const recommendationCode = recommendation.fund_code.trim().padStart(6, "0");
  return (
    CURRENT_DISCOVERY_AMOUNT_SEMANTICS.has(allocation?.amount_semantics ?? "") &&
    allocation?.revalidation_required === true &&
    recommendationAmount != null &&
    recommendationAmount > 0 &&
    allocationAmount != null &&
    Math.abs(recommendationAmount - allocationAmount) < 0.01 &&
    allocationCode === recommendationCode
  );
}

function visibleDecisionPoints(
  values: string[] | null | undefined,
  finalAction: string,
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  let hasFinalProjection = false;
  for (const raw of values ?? []) {
    const value = raw.trim();
    if (!value) continue;
    if (/^系统校验后(?:的)?最终动作(?:调整为)?\s*[：:]?/.test(value)) {
      hasFinalProjection = true;
      continue;
    }
    const key = value.replace(/\s+/g, " ");
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(value);
  }
  if (hasFinalProjection) {
    result.push(`系统校验后的最终动作：${finalAction}。`);
  }
  return result;
}

function resolveAllocationPlan(report: FundDiscoveryReport) {
  if (report.allocation_plan && Object.keys(report.allocation_plan).length) {
    return report.allocation_plan;
  }
  if (
    report.discovery_facts?.allocation_plan &&
    Object.keys(report.discovery_facts.allocation_plan).length
  ) {
    return report.discovery_facts.allocation_plan;
  }
  return undefined;
}

function candidateSectorIdentityEligible(
  candidate?: DiscoveryCandidatePoolItem,
): boolean | undefined {
  if (!candidate) return undefined;
  const status = candidate.sector_identity_status;
  const eligible = candidate.sector_identity_eligible;
  const kind = candidate.sector_match_kind;
  const mappingVerified = candidate.sector_mapping_verified;

  // Any explicit contradiction fails closed. New reports emit these fields
  // consistently; this also keeps malformed or partially migrated rows from
  // becoming executable through one optimistic flag.
  if (status && status !== "verified") return false;
  if (eligible === false) return false;
  if (kind && ["name", "new_issue", "fallback"].includes(kind)) return false;
  if (mappingVerified === false) return false;

  if (
    status === "verified" ||
    eligible === true ||
    (kind && ["primary", "tracking_exact"].includes(kind)) ||
    mappingVerified === true
  ) {
    return true;
  }
  // Historical reports predate identity provenance and encoded the old result
  // only in sector_fit_score. New reports always take one of the branches above.
  if (typeof candidate.sector_fit_score === "number") {
    return candidate.sector_fit_score >= 18;
  }
  return undefined;
}

function recommendationStatus(
  report: FundDiscoveryReport,
  recommendation: DiscoveryRecommendation,
): DiscoveryCandidateDecisionStatus {
  const code = recommendation.fund_code;
  const evidenceGuard = report.discovery_facts?.data_evidence_guard;
  if (evidenceGuard?.blocked_fund_codes?.includes(code)) {
    return "watch_only";
  }

  const candidate = report.candidate_pool?.find((item) => item.fund_code === code);
  const sectorIdentityEligible = candidateSectorIdentityEligible(candidate);
  const qualityGate = candidate?.quality_gate;
  if (qualityGate && (!qualityGate.eligible || qualityGate.status !== "eligible")) {
    return "watch_only";
  }
  if (candidate?.vehicle_quality_status && candidate.vehicle_quality_status !== "eligible") {
    return "watch_only";
  }
  if (sectorIdentityEligible === false) {
    return "watch_only";
  }

  const allocationPlan = resolveAllocationPlan(report);
  const hasDeterministicAllocationPlan = Boolean(allocationPlan);
  if (
    hasDeterministicAllocationPlan &&
    EXECUTABLE_DISCOVERY_ACTIONS.has(recommendation.action) &&
    (!CURRENT_DISCOVERY_AMOUNT_SEMANTICS.has(
      allocationPlan?.amount_semantics ?? "",
    ) || !isCurrentDiscoveryAllocation(recommendation))
  ) {
    return "watch_only";
  }

  const event = report.decision_events?.find((item) => item.fund_code === code);
  const category = event?.action_category ?? event?.evaluation_class;
  if (category === "buy" && event?.eligible !== false) {
    return "actionable";
  }
  if (category === "conditional_wait") {
    return "conditional_wait";
  }
  if (category === "watch_only" || category === "invalid" || event?.eligible === false) {
    return "watch_only";
  }

  if (recommendation.action === "等待回调") {
    return "conditional_wait";
  }
  if (EXECUTABLE_DISCOVERY_ACTIONS.has(recommendation.action)) {
    return "actionable";
  }
  return "watch_only";
}

function DiscoveryRecommendationCard({
  rec,
  candidate,
  onOpenFund,
  compact = false,
}: {
  rec: DiscoveryRecommendation;
  candidate?: DiscoveryCandidatePoolItem;
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
  compact?: boolean;
}) {
  const actionDisplayLabel = discoveryActionDisplayLabel(rec);
  const currentDiscoveryAllocation = isCurrentDiscoveryAllocation(rec);
  const sectorIdentityEligible = candidateSectorIdentityEligible(candidate);
  const fundEvidenceComplete = Boolean(
    candidate?.quality_gate?.eligible
    && candidate.quality_gate.status === "eligible"
    && candidate.vehicle_quality_status === "eligible"
    && sectorIdentityEligible === true,
  );
  const fundEvidenceFailed = Boolean(
    candidate
    && (
      candidate.quality_gate?.status === "watch_only"
      || candidate.quality_gate?.status === "excluded"
      || (candidate.vehicle_quality_status && candidate.vehicle_quality_status !== "eligible")
      || sectorIdentityEligible === false
    ),
  );
  const fundEvidenceSummary = !candidate
    ? null
    : fundEvidenceComplete
      ? { label: "基金证据通过", className: "status-info ring-1 ring-[var(--info-border)]" }
      : fundEvidenceFailed
        ? { label: "基金证据待加强", className: "status-warn ring-1 ring-[var(--warn-border)]" }
        : { label: "基金资料待复核", className: "status-neutral ring-1 ring-[var(--line)]" };
  const decisionPoints = visibleDecisionPoints(rec.points, actionDisplayLabel);
  const hasProfessionalDetails = Boolean(
    rec.decision_path ||
      rec.sector_evidence?.length ||
      rec.fund_evidence?.length ||
      rec.validation_notes?.length ||
      decisionPoints.length > 1 ||
      (rec.risks?.length ?? 0) > 1 ||
      (!currentDiscoveryAllocation && rec.suggested_amount_yuan != null),
  );
  return (
    <article className={`rounded-2xl border bg-white shadow-sm ${
      compact ? "border-slate-200/80 p-3.5" : "border-slate-200 p-4"
    }`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <button
          type="button"
          onClick={() => onOpenFund?.(rec)}
          className="min-h-11 min-w-0 rounded-lg text-left transition hover:text-[var(--brand-strong)]"
        >
          <div className="break-words text-sm font-bold text-slate-900">
            [{rec.fund_code}] {rec.fund_name}
          </div>
          <div className="mt-1 break-words text-xs text-slate-500">
            {rec.sector_name}
            {rec.hold_horizon ? ` · 持有期 ${rec.hold_horizon}` : ""}
            {rec.confidence ? ` · 置信度 ${rec.confidence}` : ""}
          </div>
          <div className="mt-1 text-[11px] font-medium text-[var(--brand)]">查看基金详情 →</div>
        </button>
        <div className="flex flex-wrap justify-end gap-1.5">
          {fundEvidenceSummary ? (
            <span className={`rounded-full px-2 py-1 text-[10px] font-black ring-1 ${fundEvidenceSummary.className}`}>
              {fundEvidenceSummary.label}
            </span>
          ) : null}
          <span className={actionBadgeClass(rec.action)}>{actionDisplayLabel}</span>
        </div>
      </div>
      {rec.suggested_amount_yuan != null && (currentDiscoveryAllocation || !compact) ? (
        <div
          aria-label={currentDiscoveryAllocation ? "本次参考金额" : "历史参考金额"}
          className={`mt-2 rounded-xl border px-3 py-2.5 ${
            currentDiscoveryAllocation
              ? "border-[var(--success-border)] bg-[var(--success-bg)]/80"
              : "border-[var(--line)] bg-[var(--surface-muted)]"
          }`}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className={`text-[11px] font-black tracking-wide ${
              currentDiscoveryAllocation ? "text-[var(--success-fg)]" : "text-slate-600"
            }`}>
              {currentDiscoveryAllocation ? "本次参考金额" : "历史参考金额"}
            </span>
            <strong className={`font-mono text-lg tabular-nums ${
              currentDiscoveryAllocation ? "text-[var(--success-fg)]" : "text-slate-900"
            }`}>
              {formatYuan(rec.suggested_amount_yuan)}
            </strong>
          </div>
          {rec.amount_note ? (
            <p className="mt-1 break-words text-[11px] leading-5 text-slate-600 [overflow-wrap:anywhere]">
              {translateEvidenceText(rec.amount_note)}
            </p>
          ) : null}
        </div>
      ) : null}
      {rec.suggested_position_change_percent != null ? (
        <DiscoveryPositionChangeBadge
          percent={rec.suggested_position_change_percent}
          basis={rec.suggested_position_change_basis}
        />
      ) : null}
      {decisionPoints[0] ? (
        <p className="mt-3 break-words text-sm leading-6 text-slate-700 [overflow-wrap:anywhere]">
          <span className="font-black text-slate-900">核心理由：</span>
          {translateEvidenceText(decisionPoints[0])}
        </p>
      ) : null}
      {(rec.risks ?? []).length ? (
        <div className="mt-3 rounded-xl bg-[var(--warn-bg)] px-3 py-2 text-xs text-[var(--warn-fg)]">
          <div className="break-words [overflow-wrap:anywhere]">⚠ {translateEvidenceText(rec.risks?.[0] ?? "")}</div>
        </div>
      ) : null}
      {hasProfessionalDetails ? (
        <details className="group mt-3 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/60">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-xs font-black text-slate-700 hover:bg-slate-100 [&::-webkit-details-marker]:hidden">
            查看完整依据
            <ChevronDown size={16} className="text-slate-500 transition group-open:rotate-180" aria-hidden />
          </summary>
          <div className="space-y-3 border-t border-slate-200 p-3">
            {!currentDiscoveryAllocation && rec.suggested_amount_yuan != null ? (
              <div aria-label="历史参考金额" className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-[11px] font-black tracking-wide text-slate-600">历史参考金额</span>
                  <strong className="font-mono text-lg tabular-nums text-slate-900">
                    {formatYuan(rec.suggested_amount_yuan)}
                  </strong>
                </div>
                <p className="mt-1 text-[11px] leading-5 text-slate-500">不作为本次参考金额。</p>
              </div>
            ) : null}
            {rec.decision_path ? (
              <div className="rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)]/70 px-3 py-2.5 text-sm leading-6 text-[var(--info-fg)]">
                <div className="text-xs font-black text-[var(--info-fg)]">决策路径</div>
                <p className="mt-1 break-words [overflow-wrap:anywhere]">{translateEvidenceText(rec.decision_path)}</p>
              </div>
            ) : null}
            <DecisionEvidenceGrid
              sectorEvidence={rec.sector_evidence}
              fundEvidence={rec.fund_evidence}
              validationNotes={rec.validation_notes}
            />
            {decisionPoints.length > 1 ? (
              <ul className="space-y-1 text-sm text-slate-700">
                {decisionPoints.slice(1).map((point, pointIndex) => (
                  <li className="break-words [overflow-wrap:anywhere]" key={`${point}-${pointIndex}`}>· {translateEvidenceText(point)}</li>
                ))}
              </ul>
            ) : null}
            {(rec.risks?.length ?? 0) > 1 ? (
              <div className="rounded-xl bg-[var(--warn-bg)] px-3 py-2 text-xs leading-5 text-[var(--warn-fg)]">
                <p className="font-black">其他风险</p>
                {(rec.risks ?? []).slice(1).map((risk, riskIndex) => (
                  <p className="mt-1 break-words [overflow-wrap:anywhere]" key={`${risk}-${riskIndex}`}>· {translateEvidenceText(risk)}</p>
                ))}
              </div>
            ) : null}
          </div>
        </details>
      ) : null}
    </article>
  );
}

function DiscoveryAllocationPlanPanel({ report }: { report: FundDiscoveryReport }) {
  const plan = resolveAllocationPlan(report);
  if (!plan) {
    return null;
  }

  const budget = plan.budget ?? {};
  const unallocated = plan.unallocated_budget ?? {};
  const risk = report.discovery_facts?.risk_context;
  const riskSummary = plan.risk_context;
  const riskStatus = risk?.status ?? riskSummary?.status ?? "unavailable";
  const riskReasonCodes = risk?.reason_codes ?? riskSummary?.reason_codes ?? [];
  const allocationNotEvaluated = riskReasonCodes.includes(
    "no_actionable_recommendation_candidates",
  );
  const riskQualified = risk
    ? risk.qualified === true && risk.status === "qualified"
    : riskStatus === "qualified";
  const metrics = [
    ["本次可投入预算", formatYuan(budget.requested_yuan)],
    ["本次投入上限", formatYuan(budget.current_tranche_cap_yuan)],
    ["本次建议投入", formatYuan(budget.allocated_current_tranche_yuan, "¥0")],
    ["本次未使用预算", formatYuan(unallocated.amount_yuan, "¥0")],
  ] as const;
  const riskSampleDays = risk?.candidate_common_return_sample_days;
  const holdingCoverage = finiteAmount(
    risk?.current_holdings_nav_amount_coverage_percent,
  );

  return (
    <section
      aria-label="本次资金安排"
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
    >
      <details className="group">
        <summary className="flex cursor-pointer list-none items-start justify-between gap-3 px-4 py-3.5 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--brand)] [&::-webkit-details-marker]:hidden">
          <div className="min-w-0">
            <h3 className="flex items-center gap-2 text-sm font-black text-slate-950">
              <CircleDollarSign size={17} aria-hidden="true" className="text-[var(--brand)]" />
              本次资金安排
            </h3>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              {plan.status === "allocated" || plan.status === "partial"
                ? `本次参考 ${formatYuan(budget.allocated_current_tranche_yuan, "¥0")}，展开查看预算与风控明细。`
                : "本次未形成参考金额，展开查看原因。"}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className={`rounded-full border px-2 py-1 text-[11px] font-black ${
              plan.status === "allocated"
                ? "status-good"
                : plan.status === "partial"
                  ? "status-warn"
                  : "status-neutral"
            }`}>
              {plan.status === "allocated"
                ? "本次已分配"
                : plan.status === "partial"
                  ? "本次部分分配"
                  : "本次未分配"}
            </span>
            <ChevronDown size={17} aria-hidden="true" className="text-slate-500 transition group-open:rotate-180" />
          </div>
        </summary>

      <dl className="grid grid-cols-2 border-y border-slate-100 sm:grid-cols-4">
        {metrics.map(([label, value]) => (
          <div key={label} className="border-b border-r border-slate-100 px-3 py-2.5 last:border-r-0 sm:border-b-0">
            <dt className="text-[10px] font-semibold text-slate-500">{label}</dt>
            <dd className="mt-1 font-mono text-sm font-black tabular-nums text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>

      <div className={`flex items-start gap-2 px-4 py-3 text-xs leading-5 ${
        riskQualified ? "bg-[var(--success-bg)]/70 text-[var(--success-fg)]" : "bg-[var(--warn-bg)] text-[var(--warn-fg)]"
      }`}>
        {riskQualified ? (
          <ShieldCheck size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-[var(--success-icon)]" />
        ) : (
          <ShieldAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-[var(--warn-icon)]" />
        )}
        <div className="min-w-0">
          <p className="font-black">
            {riskQualified
              ? "组合风险上下文已通过"
              : allocationNotEvaluated
                ? "暂无进入金额分配的买入候选"
                : "组合风险上下文未通过或未记录"}
          </p>
          <p className="mt-0.5 opacity-80">
            {riskQualified
              ? [
                  riskSampleDays != null ? `候选共同收益样本 ${riskSampleDays} 日` : null,
                  holdingCoverage != null ? `当前持仓净值金额覆盖 ${holdingCoverage}%` : null,
                ].filter(Boolean).join(" · ") || "已完成风险协方差与持仓相关性核验"
              : allocationNotEvaluated
                ? "候选筛选阶段已止步，组合风险分配未运行；这不表示风险校验失败。"
                : "风险证据不合格时不生成本次参考金额。"}
          </p>
        </div>
      </div>

      <p className="border-t border-slate-100 px-4 py-2.5 text-[11px] font-semibold leading-5 text-slate-600">
        买入并录入持仓后，后续加减仓由日报基于最新持仓与风险重新分析。
      </p>
      </details>
    </section>
  );
}

function RecommendationGroup({
  id,
  title,
  description,
  recommendations,
  candidateByCode,
  onOpenFund,
  collapsible = false,
}: {
  id: string;
  title: string;
  description: string;
  recommendations: DiscoveryRecommendation[];
  candidateByCode: Map<string, DiscoveryCandidatePoolItem>;
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
  collapsible?: boolean;
}) {
  const [open, setOpen] = useState(!collapsible);
  if (!recommendations.length) {
    return null;
  }
  return (
    <section className="grid gap-3" aria-labelledby={id}>
      <div className="flex items-end justify-between gap-3 px-1">
        <div>
          <h3 id={id} className="text-base font-black text-slate-950">{title}</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
        </div>
        {collapsible ? (
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls={`${id}-content`}
            className="inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 shadow-sm hover:bg-slate-50"
          >
            {open ? "收起" : `查看 ${recommendations.length} 只`}
            <ChevronDown size={14} aria-hidden="true" className={`transition ${open ? "rotate-180" : ""}`} />
          </button>
        ) : (
          <span className="shrink-0 text-xs font-bold text-slate-500">{recommendations.length} 只</span>
        )}
      </div>
      {open ? (
        <div id={`${id}-content`} className="grid gap-3">
          {recommendations.map((rec, recommendationIndex) => (
            <DiscoveryRecommendationCard
              key={`${rec.fund_code}-${recommendationIndex}`}
              rec={rec}
              candidate={candidateByCode.get(rec.fund_code)}
              onOpenFund={onOpenFund}
              compact={collapsible}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function DiscoveryReportPanel({ report, onOpenFund }: DiscoveryReportPanelProps) {
  const candidateByCode = useMemo(
    () => new Map(
      (report.candidate_pool ?? []).map((item) => [item.fund_code, item]),
    ),
    [report.candidate_pool],
  );
  const mainlineSnapshot = report.discovery_facts?.mainline_snapshot;
  const sectorOpportunities = useMemo(() => {
    const regimesByLabel = new Map(
      (mainlineSnapshot?.sectors ?? [])
        .filter((item) => Boolean(item.sector_label))
        .map((item) => [item.sector_label as string, item]),
    );
    return (report.discovery_facts?.sector_opportunities ?? []).map((item) => ({
      ...item,
      mainline_regime: regimesByLabel.get(item.sector_label) ?? item.mainline_regime,
    }));
  }, [mainlineSnapshot, report.discovery_facts?.sector_opportunities]);
  const hasEntryMaturity =
    isEntryMaturityPolicy(mainlineSnapshot?.entry_policy_version)
    || sectorOpportunities.some((item) => isEntryMaturityPolicy(item.score_policy_version));
  const directionGroups = useMemo(() => ({
    ready: sectorOpportunities.filter((item) => item.entry_state === "ready_to_start"),
    early: sectorOpportunities.filter((item) => item.probability_early_probe_eligible === true),
    pullback: sectorOpportunities.filter((item) => item.entry_state === "ready_on_pullback"),
    research: sectorOpportunities.filter(
      (item) => item.probability_early_probe_eligible !== true
        && !["ready_to_start", "ready_on_pullback"].includes(item.entry_state ?? "forming"),
    ),
  }), [sectorOpportunities]);
  const recommendationScope = report.discovery_facts?.recommendation_candidate_scope;
  const unmatchedActionableSectors =
    recommendationScope?.unmatched_actionable_sector_labels ?? [];
  const [chatOpen, setChatOpen] = useState(false);
  const [outcomesOpen, setOutcomesOpen] = useState(false);
  const [directionsOpen, setDirectionsOpen] = useState(true);
  const chatDrawerId = `discovery-report-chat-${report.id}`;
  const directionsContentId = `discovery-direction-content-${report.id}`;
  const groupedRecommendations = useMemo(() => {
    const actionable: DiscoveryRecommendation[] = [];
    const conditionalWait: DiscoveryRecommendation[] = [];
    const watchOnly: DiscoveryRecommendation[] = [];
    const decisionStatusByCode: Record<string, DiscoveryCandidateDecisionStatus> = {};
    const decisionReasonsByCode: Record<string, string[]> = {};
    const recommendationCodes = new Set(
      (report.recommendations ?? []).map((item) => item.fund_code),
    );
    const poolCodes = new Set(
      (report.candidate_pool ?? []).map((item) => item.fund_code),
    );

    for (const decision of recommendationScope?.candidate_decisions ?? []) {
      if (!decision.fund_code || (poolCodes.size && !poolCodes.has(decision.fund_code))) {
        continue;
      }
      // Scope "actionable" means the candidate may enter the final guard. It
      // is not itself a buy decision: only a post-guard recommendation/event
      // can make the user-facing status actionable.
      decisionStatusByCode[decision.fund_code] =
        decision.status === "actionable" && !recommendationCodes.has(decision.fund_code)
          ? "watch_only"
          : decision.status;
      decisionReasonsByCode[decision.fund_code] = decision.reason_codes ?? [];
      if (decision.status === "actionable" && !recommendationCodes.has(decision.fund_code)) {
        decisionReasonsByCode[decision.fund_code] = [
          "final_recommendation_not_available",
          ...decisionReasonsByCode[decision.fund_code],
        ];
      }
    }

    for (const recommendation of report.recommendations ?? []) {
      const status = recommendationStatus(report, recommendation);
      decisionStatusByCode[recommendation.fund_code] = status;
      if (status === "actionable") {
        actionable.push(recommendation);
      } else if (status === "conditional_wait") {
        conditionalWait.push(recommendation);
      } else {
        watchOnly.push(recommendation);
      }
    }

    const decisionCounts = Object.values(decisionStatusByCode).reduce(
      (counts, status) => {
        counts[status] += 1;
        return counts;
      },
      { actionable: 0, conditional_wait: 0, watch_only: 0 },
    );

    return {
      actionable,
      conditionalWait,
      watchOnly,
      decisionStatusByCode,
      decisionReasonsByCode,
      decisionCounts,
    };
  }, [recommendationScope?.candidate_decisions, report]);
  const selectedCodes = groupedRecommendations.actionable.map((item) => item.fund_code);
  const blockedCount = report.discovery_facts?.data_evidence_guard?.blocked_fund_codes?.length ?? 0;
  const visibleCaveats = useMemo(
    () => (report.caveats ?? []).filter((line) => !isObsoleteDiscoveryCashCaveat(line)),
    [report.caveats],
  );
  const discoveryStrategy =
    report.discovery_facts?.effective_configuration?.discovery_strategy;
  const strategySummary = discoveryStrategy === "opportunity_first"
    ? "机会优先 · 高弹性20～60交易日 · 回撤不参与机会排序"
    : discoveryStrategy === "risk_first"
      ? "稳健筛选 · 历史波动与量化覆盖执行严格门槛"
      : null;
  const decisionHeadline = groupedRecommendations.actionable.length
    ? `${groupedRecommendations.actionable.length} 只形成买入建议`
    : "本次暂无买入建议";
  const nextStep = groupedRecommendations.actionable.length
    ? "先查看推荐基金和本次参考金额；确认可申购后，买入并录入持仓，后续加减仓由日报分析。"
    : groupedRecommendations.decisionCounts.conditional_wait
      ? "先等待设定条件出现，下一次扫描会重新判断；现在无需买入。"
      : "把这些基金加入观察即可；关键资料补齐前，不需要采取买入动作。";

  return (
    <div className="grid min-w-0 gap-5">
      <section
        data-testid="discovery-decision-summary"
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="bg-[linear-gradient(135deg,#071f29_0%,#123847_65%,#176b70_145%)] px-5 py-5 text-white sm:px-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 max-w-3xl">
              <p className="text-[10px] font-black tracking-[0.2em] text-[var(--accent-soft)]/75">DISCOVERY BRIEF · 荐基决策简报</p>
              <h2 className="font-display mt-2 text-xl font-extrabold leading-tight text-white sm:text-2xl">{report.title}</h2>
              <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-200">{report.summary}</p>
              {strategySummary ? (
                <p className="mt-3 inline-flex rounded-full border border-white/20 bg-white/10 px-3 py-1 text-[11px] font-black text-[var(--accent-soft)]">
                  {strategySummary}
                </p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => setChatOpen(true)}
              className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-xl border border-white/15 bg-white/10 px-3 text-xs font-black text-white transition hover:bg-white/15"
              aria-expanded={chatOpen}
              aria-controls={chatDrawerId}
              aria-haspopup="dialog"
            >
              <MessageCircle size={16} aria-hidden="true" />
              追问本次推荐
            </button>
          </div>
        </div>

        <div className="grid gap-4 px-5 py-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
          <div className="min-w-0">
            <div className="flex items-start gap-3">
              <span className={`mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-full ${
                groupedRecommendations.actionable.length
                  ? "bg-[var(--success-bg)] text-[var(--success-icon)]"
                  : "bg-[var(--warn-bg)] text-[var(--warn-icon)]"
              }`}>
                {groupedRecommendations.actionable.length ? <ShieldCheck size={19} /> : <ShieldAlert size={19} />}
              </span>
              <div>
                <h3 className="text-base font-black text-slate-950">{decisionHeadline}</h3>
                <p className="mt-1 text-sm leading-6 text-slate-600">
                  {blockedCount > 0
                    ? `有 ${blockedCount} 只候选的关键资料不完整或不够新，系统已保守列为“观察”；资料补齐前不会建议买入。`
                    : groupedRecommendations.actionable.length
                      ? "以下候选已通过方向、入场时机、数据时点、基金质量与组合风险校验。"
                      : "候选尚未同时通过方向、入场时机、数据质量和组合风险校验，因此不建议直接买入。"}
                </p>
              </div>
            </div>
            <div className="mt-3 rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-700">
              <span className="font-black text-slate-950">下一步：</span>{nextStep}
            </div>
          </div>

          <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-xl bg-slate-200 ring-1 ring-slate-200 lg:min-w-[280px]">
            {[
              ["建议买入", groupedRecommendations.decisionCounts.actionable, "text-[var(--success-fg)]"],
              ["等条件", groupedRecommendations.decisionCounts.conditional_wait, "text-[var(--warn-fg)]"],
              ["仅观察", groupedRecommendations.decisionCounts.watch_only, "text-slate-700"],
            ].map(([label, value, className]) => (
              <div key={String(label)} className="bg-white px-3 py-2.5 text-center">
                <dt className="text-[10px] font-bold text-slate-500">{label}</dt>
                <dd className={`mt-1 font-mono text-lg font-black tabular-nums ${className}`}>{value}</dd>
              </div>
            ))}
          </dl>
        </div>

        {report.market_view || report.target_sectors?.length ? (
          <details className="group border-t border-slate-100">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-5 text-xs font-black text-slate-600 hover:bg-slate-50 sm:px-6 [&::-webkit-details-marker]:hidden">
              展开市场判断与扫描范围
              <ChevronDown size={15} aria-hidden="true" className="transition group-open:rotate-180" />
            </summary>
            <div className="space-y-2 border-t border-slate-100 bg-slate-50/60 px-5 py-3 text-sm leading-6 text-slate-700 sm:px-6">
              {report.market_view ? <p><span className="font-black text-slate-900">市场判断：</span>{report.market_view}</p> : null}
              {report.target_sectors?.length ? <p className="text-xs text-slate-500">扫描范围：{report.target_sectors.join("、")}</p> : null}
            </div>
          </details>
        ) : null}
      </section>

      <DiscoveryAllocationPlanPanel report={report} />

      <RecommendationGroup
        id="discovery-actionable-title"
        title="推荐基金"
        description="优先看本次参考金额、核心理由和主要风险；买入后的加减仓交给持仓日报。"
        recommendations={groupedRecommendations.actionable}
        candidateByCode={candidateByCode}
        onOpenFund={onOpenFund}
      />
      <RecommendationGroup
        id="discovery-conditional-title"
        title="等待条件"
        description="条件未满足前不执行，等待回调或下一次数据验证。"
        recommendations={groupedRecommendations.conditionalWait}
        candidateByCode={candidateByCode}
        onOpenFund={onOpenFund}
      />

      {hasEntryMaturity ? (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-4 py-3.5">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <h3 className="text-sm font-black text-slate-950">今日可布局方向</h3>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  同时展示成熟方向与概率提前试仓方向；后者只开放更小的本次金额，并须基金自身信号通过。
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                <span className="rounded-full bg-slate-950 px-2.5 py-1 text-[11px] font-black text-white">
                  {directionGroups.early.length
                    ? `${directionGroups.ready.length} 个成熟 · ${directionGroups.early.length} 个提前试仓`
                    : `${directionGroups.ready.length} 个通过入场线`}
                </span>
                <button
                  type="button"
                  onClick={() => setDirectionsOpen((value) => !value)}
                  aria-expanded={directionsOpen}
                  aria-controls={directionsContentId}
                  aria-label={directionsOpen ? "收起今日可布局方向" : "展开今日可布局方向"}
                  className="inline-flex size-9 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)]"
                >
                  <ChevronDown
                    size={18}
                    aria-hidden="true"
                    className={`transition ${directionsOpen ? "rotate-180" : ""}`}
                  />
                </button>
              </div>
            </div>
          </div>

          {directionsOpen ? (
            <div
              id={directionsContentId}
              data-testid="discovery-direction-content"
              className="p-4"
            >
            {directionGroups.ready.length ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {directionGroups.ready.map((item, index) => (
                  <SectorOpportunityCard
                    key={`${item.sector_label}-ready-${index}`}
                    item={item}
                    collapsibleDetails
                  />
                ))}
              </div>
            ) : directionGroups.early.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-4">
                <div className="text-sm font-black text-slate-800">今天没有方向通过当前入场线</div>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  系统不会拿当日热门板块凑数。可以继续查看等待条件，但在触发前不生成本次买入动作。
                </p>
              </div>
            ) : null}

            {directionGroups.early.length ? (
              <div className="mt-3 rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)]/35 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="text-xs font-black text-[var(--info-fg)]">
                      提前试仓方向 · {directionGroups.early.length} 个
                    </div>
                    <p className="mt-0.5 text-[11px] leading-4 text-slate-500">
                      趋势尚未完全确认，按形成概率配置计划仓位的25%～40%，失效即停止新增。
                    </p>
                  </div>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {directionGroups.early.map((item, index) => (
                    <SectorOpportunityCard
                      key={`${item.sector_label}-early-${index}`}
                      item={item}
                      collapsibleDetails
                    />
                  ))}
                </div>
              </div>
            ) : null}

            {recommendationScope?.policy_enforced ? (
              <div
                data-testid="discovery-direction-fund-scope"
                className="mt-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5"
              >
                <div className="flex items-start gap-2">
                  <ShieldCheck size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-[var(--success-icon)]" />
                  <div className="min-w-0 text-xs leading-5 text-slate-600">
                    <p className="font-black text-slate-800">方向与基金已联动筛选</p>
                    <p>
                      推荐基金只从可布局或满足提前试仓条件的方向中产生，并继续校验基金质量、载体质量和板块身份；等待方向不会用于补位。
                    </p>
                    {unmatchedActionableSectors.length ? (
                      <p className="mt-1 font-bold text-[var(--warn-fg)]">
                        暂无合格基金载体：{unmatchedActionableSectors.join("、")}。系统保留方向机会，但不会拿其他等待方向的基金凑数。
                      </p>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}

            {directionGroups.pullback.length ? (
              <details className="group mt-3 rounded-xl border border-[var(--warn-border)] bg-[var(--warn-bg)]/40">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-xs font-black text-[var(--warn-fg)] [&::-webkit-details-marker]:hidden">
                  等待入场条件 · {directionGroups.pullback.length} 个方向
                  <ChevronDown size={15} aria-hidden="true" className="transition group-open:rotate-180" />
                </summary>
                <div className="grid gap-2 border-t border-[var(--warn-border)] p-3 sm:grid-cols-2">
                  {directionGroups.pullback.map((item, index) => (
                    <SectorOpportunityCard
                      key={`${item.sector_label}-pullback-${index}`}
                      item={item}
                      collapsibleDetails
                    />
                  ))}
                </div>
              </details>
            ) : null}

            {directionGroups.research.length ? (
              <details className="group mt-3 rounded-xl border border-slate-200 bg-slate-50/60">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-xs font-black text-slate-700 [&::-webkit-details-marker]:hidden">
                  方向观察池 · {directionGroups.research.length} 个尚在形成
                  <ChevronDown size={15} aria-hidden="true" className="transition group-open:rotate-180" />
                </summary>
                <div className="grid gap-2 border-t border-slate-200 p-3 sm:grid-cols-2">
                  {directionGroups.research.map((item, index) => (
                    <SectorOpportunityCard
                      key={`${item.sector_label}-research-${index}`}
                      item={item}
                      collapsibleDetails
                    />
                  ))}
                </div>
              </details>
            ) : null}
            </div>
          ) : null}
        </section>
      ) : sectorOpportunities.length ? (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-black text-slate-950">本次主方向</h3>
            <span className="text-xs font-medium text-slate-500">
              {mainlineSnapshot?.schema_version
                ? "主线雷达仅参与研究排序 · 默认展示前 2 个方向"
                : "默认只展示评分最高的 2 个方向"}
            </span>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {sectorOpportunities.slice(0, 2).map((item, opportunityIndex) => (
              <SectorOpportunityCard key={`${item.sector_label}-${item.track ?? "track"}-${opportunityIndex}`} item={item} />
            ))}
          </div>
          {sectorOpportunities.length > 2 ? (
            <details className="group mt-3 rounded-xl border border-slate-200 bg-slate-50/60">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-xs font-black text-slate-700 [&::-webkit-details-marker]:hidden">
                查看另外 {sectorOpportunities.length - 2} 个研究方向
                <ChevronDown size={15} aria-hidden="true" className="transition group-open:rotate-180" />
              </summary>
              <div className="grid gap-2 border-t border-slate-200 p-3 sm:grid-cols-2">
                {sectorOpportunities.slice(2).map((item, opportunityIndex) => (
                  <SectorOpportunityCard key={`${item.sector_label}-${item.track ?? "track"}-${opportunityIndex + 2}`} item={item} />
                ))}
              </div>
            </details>
          ) : null}
        </section>
      ) : null}

      <RecommendationGroup
        id="discovery-watch-title"
        title="研究观察"
        description="仅保留研究线索，不构成买入建议；默认收起以减少干扰。"
        recommendations={groupedRecommendations.watchOnly}
        candidateByCode={candidateByCode}
        onOpenFund={onOpenFund}
        collapsible
      />

      <section className="grid gap-3" aria-labelledby="discovery-research-library-title">
        <div className="px-1">
          <h3 id="discovery-research-library-title" className="flex items-center gap-2 text-base font-black text-slate-950">
            <BookOpenCheck size={18} aria-hidden="true" className="text-[var(--brand)]" />
            专业研究资料
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">用于复核结论的专业资料，平时无需逐项阅读。</p>
        </div>

        {report.candidate_pool?.length ? (
          <DiscoveryCandidatePoolPanel
            pool={report.candidate_pool}
            selectedCodes={selectedCodes}
            decisionStatusByCode={groupedRecommendations.decisionStatusByCode}
            decisionReasonsByCode={groupedRecommendations.decisionReasonsByCode}
            eliminatedCandidates={report.eliminated_candidates}
          />
        ) : null}

        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <button
            type="button"
            onClick={() => setOutcomesOpen((value) => !value)}
            className="flex min-h-14 w-full items-center justify-between gap-3 px-4 text-left"
            aria-expanded={outcomesOpen}
            aria-controls="discovery-outcomes-content"
          >
            <span className="min-w-0">
              <span className="flex items-center gap-2 text-sm font-black text-slate-900">
                <BarChart3 size={17} aria-hidden="true" className="text-[var(--brand)]" />
                历史效果复盘
              </span>
              <span className="mt-1 block text-xs text-slate-500">按 T+5 / T+20 / T+60 检查历史推荐表现，展开后再加载。</span>
            </span>
            <ChevronDown size={17} aria-hidden="true" className={`shrink-0 text-slate-500 transition ${outcomesOpen ? "rotate-180" : ""}`} />
          </button>
          {outcomesOpen ? (
            <div id="discovery-outcomes-content" className="border-t border-slate-100 p-3">
              <DiscoveryOutcomesPanel reportId={report.id} />
            </div>
          ) : null}
        </section>

        {visibleCaveats.length ? (
          <details className="group rounded-2xl border border-[var(--warn-border)] bg-[var(--warn-bg)]/70">
            <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-4 text-xs font-black text-[var(--warn-fg)] [&::-webkit-details-marker]:hidden">
              使用边界与免责声明（{visibleCaveats.length} 条）
              <ChevronDown size={15} aria-hidden="true" className="transition group-open:rotate-180" />
            </summary>
            <div className="space-y-1 border-t border-[var(--warn-border)] px-4 py-3 text-xs leading-5 text-[var(--warn-fg)]">
              {visibleCaveats.map((line, lineIndex) => (
                <p className="break-words [overflow-wrap:anywhere]" key={`${line}-${lineIndex}`}>{translateEvidenceText(line)}</p>
              ))}
            </div>
          </details>
        ) : null}
      </section>

      <DiscoveryChatDrawer
        id={chatDrawerId}
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        reportId={report.id}
        reportTitle={report.title}
      />
    </div>
  );
}

