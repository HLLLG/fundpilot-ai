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
  DiscoveryEntryTrigger,
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
import { DiscoveryEntryTriggerCard } from "@/components/DiscoveryEntryTriggerCard";
import {
  DiscoveryQuantPreviewBadge,
  DiscoveryQuantPreviewCard,
} from "@/components/DiscoveryQuantPreviewCard";
import { FundTradeabilityEvidence } from "@/components/FundTradeabilityEvidence";
import { SectorOpportunityCard } from "@/components/SectorOpportunityCard";

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

function finiteAmount(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function formatYuan(value: number | null | undefined, fallback = "未确认"): string {
  const amount = finiteAmount(value);
  return amount == null ? fallback : `¥${amount.toLocaleString("zh-CN")}`;
}

function isCurrentVerifiedAllocation(recommendation: DiscoveryRecommendation): boolean {
  const recommendationAmount = finiteAmount(recommendation.suggested_amount_yuan);
  const allocationAmount = finiteAmount(
    recommendation.allocation?.suggested_amount_yuan,
  );
  const allocationCode = recommendation.allocation?.fund_code?.trim().padStart(6, "0");
  const recommendationCode = recommendation.fund_code.trim().padStart(6, "0");
  const futureTranches = recommendation.allocation?.future_tranches ?? [];
  return (
    recommendation.allocation?.amount_semantics === "current_verified_initial_tranche" &&
    recommendation.allocation.revalidation_required === true &&
    recommendationAmount != null &&
    recommendationAmount > 0 &&
    allocationAmount != null &&
    Math.abs(recommendationAmount - allocationAmount) < 0.01 &&
    allocationCode === recommendationCode &&
    futureTranches.length > 0 &&
    futureTranches.every(
      (item) => item.amount_yuan == null && item.revalidation_required === true,
    )
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

function recommendationStatus(
  report: FundDiscoveryReport,
  recommendation: DiscoveryRecommendation,
): DiscoveryCandidateDecisionStatus {
  const code = recommendation.fund_code;
  const evidenceGuard = report.discovery_facts?.data_evidence_guard;
  if (evidenceGuard?.blocked_fund_codes?.includes(code)) {
    return "watch_only";
  }

  const qualityGate = report.candidate_pool?.find((item) => item.fund_code === code)?.quality_gate;
  if (qualityGate && (!qualityGate.eligible || qualityGate.status !== "eligible")) {
    return "watch_only";
  }

  const tradeabilityGate =
    recommendation.tradeability_gate ??
    recommendation.tradeability?.tradeability_gate ??
    recommendation.cost_assessment?.tradeability_gate;
  if (tradeabilityGate?.status && tradeabilityGate.status !== "eligible") {
    return "watch_only";
  }
  if (recommendation.cost_assessment?.executable === false) {
    return "watch_only";
  }

  const allocationPlan = resolveAllocationPlan(report);
  const hasDeterministicAllocationPlan = Boolean(allocationPlan);
  if (
    hasDeterministicAllocationPlan &&
    EXECUTABLE_DISCOVERY_ACTIONS.has(recommendation.action) &&
    (allocationPlan?.amount_semantics !== "current_verified_initial_tranche" ||
      !isCurrentVerifiedAllocation(recommendation))
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
  onOpenFund,
  compact = false,
}: {
  rec: DiscoveryRecommendation;
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
  compact?: boolean;
}) {
  const verifiedInitialTranche = isCurrentVerifiedAllocation(rec);
  const futureTranche = rec.allocation?.future_tranches?.find(
    (item) => item.revalidation_required !== false,
  );
  const tradeabilityGate =
    rec.tradeability_gate ??
    rec.tradeability?.tradeability_gate ??
    rec.cost_assessment?.tradeability_gate;
  const hasTradeabilityEvidence = Boolean(
    (rec.tradeability && Object.keys(rec.tradeability).length) ||
      (tradeabilityGate && Object.keys(tradeabilityGate).length) ||
      (rec.cost_assessment && Object.keys(rec.cost_assessment).length),
  );
  const tradeabilitySummary = tradeabilityGate?.status === "eligible"
    ? { label: "交易条件通过", className: "bg-emerald-50 text-emerald-800 ring-emerald-200" }
    : tradeabilityGate?.status
      ? { label: "交易条件需复核", className: "bg-amber-50 text-amber-900 ring-amber-200" }
      : hasTradeabilityEvidence
        ? { label: "交易信息待核验", className: "bg-slate-50 text-slate-700 ring-slate-200" }
        : null;
  const decisionPoints = visibleDecisionPoints(rec.points, rec.action);
  const tradeabilityExecutionRelevant =
    EXECUTABLE_DISCOVERY_ACTIONS.has(rec.action) && tradeabilityGate?.status === "eligible";
  const hasProfessionalDetails = Boolean(
    hasTradeabilityEvidence ||
      rec.decision_path ||
      rec.sector_evidence?.length ||
      rec.fund_evidence?.length ||
      rec.validation_notes?.length ||
      decisionPoints.length > 1 ||
      (rec.risks?.length ?? 0) > 1 ||
      (!verifiedInitialTranche && rec.suggested_amount_yuan != null),
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
          <DiscoveryQuantPreviewBadge preview={rec.quant_preview} />
          {tradeabilitySummary ? (
            <span className={`rounded-full px-2 py-1 text-[10px] font-black ring-1 ${tradeabilitySummary.className}`}>
              {tradeabilitySummary.label}
            </span>
          ) : null}
          <span className={actionBadgeClass(rec.action)}>{rec.action}</span>
        </div>
      </div>
      {rec.suggested_amount_yuan != null && (verifiedInitialTranche || !compact) ? (
        <div
          aria-label={verifiedInitialTranche ? "当前已验证首批金额" : "历史参考金额"}
          className={`mt-2 rounded-xl border px-3 py-2.5 ${
            verifiedInitialTranche
              ? "border-emerald-200 bg-emerald-50/80"
              : "border-slate-200 bg-slate-50"
          }`}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className={`text-[11px] font-black tracking-wide ${
              verifiedInitialTranche ? "text-emerald-800" : "text-slate-600"
            }`}>
              {verifiedInitialTranche ? "当前已验证首批" : "历史参考金额"}
            </span>
            <strong className={`font-mono text-lg tabular-nums ${
              verifiedInitialTranche ? "text-emerald-950" : "text-slate-900"
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
      {verifiedInitialTranche && futureTranche ? (
        <div className="mt-2 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-900">
          <ShieldAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          <p>
            <span className="font-black">后续批次待重新核验 · 金额留空</span>
            <span className="block text-amber-800">
              交易条件、可用现金、板块敞口与组合风险需在执行前重新计算。
            </span>
          </p>
        </div>
      ) : null}
      {rec.suggested_position_change_percent != null ? (
        <DiscoveryPositionChangeBadge
          percent={rec.suggested_position_change_percent}
          basis={rec.suggested_position_change_basis}
        />
      ) : null}
      {rec.action === "等待回调" ? (
        <DiscoveryEntryTriggerCard trigger={rec.entry_trigger} />
      ) : null}
      <DiscoveryQuantPreviewCard preview={rec.quant_preview} compact={compact} />
      {decisionPoints[0] ? (
        <p className="mt-3 break-words text-sm leading-6 text-slate-700 [overflow-wrap:anywhere]">
          <span className="font-black text-slate-900">核心理由：</span>
          {translateEvidenceText(decisionPoints[0])}
        </p>
      ) : null}
      {(rec.risks ?? []).length ? (
        <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <div className="break-words [overflow-wrap:anywhere]">⚠ {translateEvidenceText(rec.risks?.[0] ?? "")}</div>
        </div>
      ) : null}
      {hasProfessionalDetails ? (
        <details className="group mt-3 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/60">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-xs font-black text-slate-700 hover:bg-slate-100 [&::-webkit-details-marker]:hidden">
            查看交易条件与完整依据
            <ChevronDown size={16} className="text-slate-500 transition group-open:rotate-180" aria-hidden />
          </summary>
          <div className="space-y-3 border-t border-slate-200 p-3">
            {!verifiedInitialTranche && rec.suggested_amount_yuan != null ? (
              <div aria-label="历史参考金额" className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-[11px] font-black tracking-wide text-slate-600">历史参考金额</span>
                  <strong className="font-mono text-lg tabular-nums text-slate-900">
                    {formatYuan(rec.suggested_amount_yuan)}
                  </strong>
                </div>
                <p className="mt-1 text-[11px] leading-5 text-slate-500">不作为本次可执行金额。</p>
              </div>
            ) : null}
            {hasTradeabilityEvidence ? (
              <FundTradeabilityEvidence
                tradeability={rec.tradeability}
                tradeabilityGate={rec.tradeability_gate}
                costAssessment={rec.cost_assessment}
                executionRelevant={tradeabilityExecutionRelevant}
              />
            ) : null}
            {rec.decision_path ? (
              <div className="rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2.5 text-sm leading-6 text-blue-950">
                <div className="text-xs font-black text-blue-900">决策路径</div>
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
              <div className="rounded-xl bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
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
  const riskQualified = risk
    ? risk.qualified === true && risk.status === "qualified"
    : riskStatus === "qualified";
  const metrics = [
    ["总预算", formatYuan(budget.requested_yuan)],
    ["已确认现金", formatYuan(budget.confirmed_cash_yuan)],
    ["当前首批上限", formatYuan(budget.current_tranche_cap_yuan)],
    ["首批已分配", formatYuan(budget.allocated_current_tranche_yuan, "¥0")],
    ["延期至后续批次", formatYuan(unallocated.deferred_future_tranches_yuan, "¥0")],
    ["当前未分配", formatYuan(unallocated.current_tranche_unallocated_yuan, "¥0")],
  ] as const;
  const cashUnavailable = finiteAmount(unallocated.unavailable_due_to_cash_yuan);
  const riskSampleDays = risk?.candidate_common_return_sample_days;
  const holdingCoverage = finiteAmount(
    risk?.current_holdings_nav_amount_coverage_percent,
  );

  return (
    <section
      aria-label="确定性首批分配"
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
    >
      <details className="group">
        <summary className="flex cursor-pointer list-none items-start justify-between gap-3 px-4 py-3.5 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--brand)] [&::-webkit-details-marker]:hidden">
          <div className="min-w-0">
            <h3 className="flex items-center gap-2 text-sm font-black text-slate-950">
              <CircleDollarSign size={17} aria-hidden="true" className="text-[var(--brand)]" />
              首批资金安排
            </h3>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              {plan.status === "allocated" || plan.status === "partial"
                ? `本次已分配 ${formatYuan(budget.allocated_current_tranche_yuan, "¥0")}，展开查看预算与风控明细。`
                : "本次未形成可执行金额，展开查看被拦截的原因。"}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className={`rounded-full border px-2 py-1 text-[11px] font-black ${
              plan.status === "allocated"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : plan.status === "partial"
                  ? "border-amber-200 bg-amber-50 text-amber-900"
                  : "border-slate-200 bg-slate-100 text-slate-700"
            }`}>
              {plan.status === "allocated"
                ? "首批已分配"
                : plan.status === "partial"
                  ? "首批部分分配"
                  : "未形成首批金额"}
            </span>
            <ChevronDown size={17} aria-hidden="true" className="text-slate-500 transition group-open:rotate-180" />
          </div>
        </summary>

      <dl className="grid grid-cols-2 border-y border-slate-100 sm:grid-cols-3">
        {metrics.map(([label, value]) => (
          <div key={label} className="border-b border-r border-slate-100 px-3 py-2.5 last:border-r-0 sm:border-b-0">
            <dt className="text-[10px] font-semibold text-slate-500">{label}</dt>
            <dd className="mt-1 font-mono text-sm font-black tabular-nums text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>

      <div className={`flex items-start gap-2 px-4 py-3 text-xs leading-5 ${
        riskQualified ? "bg-emerald-50/70 text-emerald-950" : "bg-amber-50 text-amber-950"
      }`}>
        {riskQualified ? (
          <ShieldCheck size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-emerald-700" />
        ) : (
          <ShieldAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-700" />
        )}
        <div className="min-w-0">
          <p className="font-black">
            {riskQualified ? "组合风险上下文已通过" : "组合风险上下文未通过或未记录"}
          </p>
          <p className="mt-0.5 opacity-80">
            {riskQualified
              ? [
                  riskSampleDays != null ? `候选共同收益样本 ${riskSampleDays} 日` : null,
                  holdingCoverage != null ? `当前持仓净值金额覆盖 ${holdingCoverage}%` : null,
                ].filter(Boolean).join(" · ") || "已完成风险协方差与持仓相关性核验"
              : "风险证据不合格时按关闭执行处理，不生成可执行金额。"}
          </p>
          {cashUnavailable != null && cashUnavailable > 0 ? (
            <p className="mt-0.5">因现金不足或未确认不可用：{formatYuan(cashUnavailable)}</p>
          ) : null}
        </div>
      </div>

      <p className="border-t border-slate-100 px-4 py-2.5 text-[11px] font-semibold leading-5 text-slate-600">
        后续批次不预设金额；执行前必须重新核验交易状态、现金、敞口与风险。
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
  onOpenFund,
  collapsible = false,
  initialLimit,
}: {
  id: string;
  title: string;
  description: string;
  recommendations: DiscoveryRecommendation[];
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
  collapsible?: boolean;
  initialLimit?: number;
}) {
  const [open, setOpen] = useState(!collapsible);
  const [showAll, setShowAll] = useState(false);
  if (!recommendations.length) {
    return null;
  }
  const visibleRecommendations =
    initialLimit && !showAll ? recommendations.slice(0, initialLimit) : recommendations;
  const hiddenCount = recommendations.length - visibleRecommendations.length;
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
        ) : hiddenCount > 0 ? (
          <button
            type="button"
            onClick={() => setShowAll(true)}
            aria-expanded={showAll}
            aria-controls={`${id}-content`}
            className="inline-flex min-h-10 shrink-0 items-center rounded-full border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 shadow-sm hover:bg-slate-50"
          >
            查看其余 {hiddenCount} 只
          </button>
        ) : (
          <span className="shrink-0 text-xs font-bold text-slate-500">{recommendations.length} 只</span>
        )}
      </div>
      {open ? (
        <div id={`${id}-content`} className="grid gap-3">
          {visibleRecommendations.map((rec, recommendationIndex) => (
            <DiscoveryRecommendationCard
              key={`${rec.fund_code}-${recommendationIndex}`}
              rec={rec}
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
  const [chatOpen, setChatOpen] = useState(false);
  const [outcomesOpen, setOutcomesOpen] = useState(false);
  const chatDrawerId = `discovery-report-chat-${report.id}`;
  const groupedRecommendations = useMemo(() => {
    const actionable: DiscoveryRecommendation[] = [];
    const conditionalWait: DiscoveryRecommendation[] = [];
    const watchOnly: DiscoveryRecommendation[] = [];
    const decisionStatusByCode: Record<string, DiscoveryCandidateDecisionStatus> = {};
    const entryTriggerByCode: Record<string, DiscoveryEntryTrigger | null | undefined> = {};
    const quantPreviewByCode: Record<string, DiscoveryRecommendation["quant_preview"]> = {};

    for (const recommendation of report.recommendations) {
      const status = recommendationStatus(report, recommendation);
      decisionStatusByCode[recommendation.fund_code] = status;
      entryTriggerByCode[recommendation.fund_code] = recommendation.entry_trigger;
      quantPreviewByCode[recommendation.fund_code] = recommendation.quant_preview;
      if (status === "actionable") {
        actionable.push(recommendation);
      } else if (status === "conditional_wait") {
        conditionalWait.push(recommendation);
      } else {
        watchOnly.push(recommendation);
      }
    }

    return {
      actionable,
      conditionalWait,
      watchOnly,
      decisionStatusByCode,
      entryTriggerByCode,
      quantPreviewByCode,
    };
  }, [report]);
  const selectedCodes = groupedRecommendations.actionable.map((item) => item.fund_code);
  const blockedCount = report.discovery_facts?.data_evidence_guard?.blocked_fund_codes?.length ?? 0;
  const discoveryStrategy =
    report.discovery_facts?.effective_configuration?.discovery_strategy;
  const strategySummary = discoveryStrategy === "opportunity_first"
    ? "机会优先 · 20～60交易日 · 历史回撤用于调整首批仓位"
    : discoveryStrategy === "risk_first"
      ? "稳健筛选 · 历史波动与量化覆盖执行严格门槛"
      : null;
  const decisionHeadline = groupedRecommendations.actionable.length
    ? `${groupedRecommendations.actionable.length} 只通过可执行校验`
    : "本次暂无可执行建议";
  const nextStep = groupedRecommendations.actionable.length
    ? "先查看可执行候选和首批金额；真正下单前再核对交易状态。"
    : groupedRecommendations.conditionalWait.length
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
              <p className="text-[10px] font-black tracking-[0.2em] text-cyan-100/75">DISCOVERY BRIEF · 荐基决策简报</p>
              <h2 className="font-display mt-2 text-xl font-extrabold leading-tight text-white sm:text-2xl">{report.title}</h2>
              <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-200">{report.summary}</p>
              {strategySummary ? (
                <p className="mt-3 inline-flex rounded-full border border-cyan-100/20 bg-white/10 px-3 py-1 text-[11px] font-black text-cyan-50">
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
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-amber-100 text-amber-900"
              }`}>
                {groupedRecommendations.actionable.length ? <ShieldCheck size={19} /> : <ShieldAlert size={19} />}
              </span>
              <div>
                <h3 className="text-base font-black text-slate-950">{decisionHeadline}</h3>
                <p className="mt-1 text-sm leading-6 text-slate-600">
                  {blockedCount > 0
                    ? `有 ${blockedCount} 只候选的关键资料不完整或不够新，系统已保守列为“观察”；资料补齐前不会建议买入。`
                    : groupedRecommendations.actionable.length
                      ? "以下候选已通过动作、数据时点、基金质量和交易条件校验，仍需由你最终确认。"
                      : "候选尚未同时通过数据、质量和交易条件校验，因此不建议直接买入。"}
                </p>
              </div>
            </div>
            <div className="mt-3 rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-700">
              <span className="font-black text-slate-950">下一步：</span>{nextStep}
            </div>
          </div>

          <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-xl bg-slate-200 ring-1 ring-slate-200 lg:min-w-[280px]">
            {[
              ["可执行", groupedRecommendations.actionable.length, "text-emerald-800"],
              ["等条件", groupedRecommendations.conditionalWait.length, "text-amber-800"],
              ["仅观察", groupedRecommendations.watchOnly.length, "text-slate-700"],
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
        title="可执行建议"
        description="优先看金额、核心理由和主要风险；交易细节按需展开。"
        recommendations={groupedRecommendations.actionable}
        onOpenFund={onOpenFund}
        initialLimit={3}
      />
      <RecommendationGroup
        id="discovery-conditional-title"
        title="等待条件"
        description="条件未满足前不执行，等待回调或下一次数据验证。"
        recommendations={groupedRecommendations.conditionalWait}
        onOpenFund={onOpenFund}
        initialLimit={3}
      />

      {sectorOpportunities.length ? (
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
            entryTriggerByCode={groupedRecommendations.entryTriggerByCode}
            quantPreviewByCode={groupedRecommendations.quantPreviewByCode}
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

        {report.caveats?.length ? (
          <details className="group rounded-2xl border border-amber-200/80 bg-amber-50/70 shadow-sm">
            <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-4 text-xs font-black text-amber-950 [&::-webkit-details-marker]:hidden">
              使用边界与免责声明（{report.caveats.length} 条）
              <ChevronDown size={15} aria-hidden="true" className="transition group-open:rotate-180" />
            </summary>
            <div className="space-y-1 border-t border-amber-200 px-4 py-3 text-xs leading-5 text-amber-900">
              {report.caveats.map((line, lineIndex) => (
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

