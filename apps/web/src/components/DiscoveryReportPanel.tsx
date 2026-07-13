"use client";

import { useMemo, useState } from "react";
import { ChevronDown, MessageCircle, TrendingDown, TrendingUp } from "lucide-react";
import type { DiscoveryRecommendation, FundDiscoveryReport } from "@/lib/api";
import { actionBadgeClass } from "@/lib/actionStyles";
import { translateEvidenceText } from "@/lib/decisionText";
import { DecisionEvidenceGrid } from "@/components/DecisionEvidenceGrid";
import {
  DiscoveryCandidatePoolPanel,
  type DiscoveryCandidateDecisionStatus,
} from "@/components/DiscoveryCandidatePoolPanel";
import { DiscoveryChatDrawer } from "@/components/DiscoveryChatDrawer";
import { DiscoveryOutcomesPanel } from "@/components/DiscoveryOutcomesPanel";
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

function recommendationStatus(
  report: FundDiscoveryReport,
  recommendation: DiscoveryRecommendation,
): DiscoveryCandidateDecisionStatus {
  const code = recommendation.fund_code;
  const scanGoal =
    report.discovery_facts?.effective_configuration?.scan_goal ??
    report.discovery_facts?.portfolio_gap?.scan_mode;
  if (scanGoal === "dip_swing") {
    return recommendation.action === "等待回调" ? "conditional_wait" : "watch_only";
  }
  const evidenceGuard = report.discovery_facts?.data_evidence_guard;
  if (evidenceGuard?.blocked_fund_codes?.includes(code)) {
    return "watch_only";
  }

  const qualityGate = report.candidate_pool?.find((item) => item.fund_code === code)?.quality_gate;
  if (qualityGate && (!qualityGate.eligible || qualityGate.status !== "eligible")) {
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
}: {
  rec: DiscoveryRecommendation;
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
}) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
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
          {(rec.dip_drop_percent != null ||
            rec.fee_break_even_percent != null ||
            rec.target_exit_days != null ||
            (rec.rebound_signals?.length ?? 0) > 0) ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {rec.dip_drop_percent != null ? (
                <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">
                  近段跌幅 {rec.dip_drop_percent.toFixed(2)}%
                </span>
              ) : null}
              {rec.fee_break_even_percent != null ? (
                <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold text-slate-700">
                  扣费止盈线 {rec.fee_break_even_percent.toFixed(2)}%
                </span>
              ) : null}
              {rec.target_exit_days != null ? (
                <span className="rounded-full border border-[var(--brand)]/30 bg-[var(--brand)]/10 px-2 py-0.5 text-[11px] font-semibold text-[var(--brand-strong)]">
                  目标 {rec.target_exit_days} 天内
                </span>
              ) : null}
              {(rec.rebound_signals ?? []).map((signal) => (
                <span
                  key={signal.id}
                  className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-900"
                >
                  {signal.label}
                </span>
              ))}
            </div>
          ) : null}
          <div className="mt-1 text-[11px] font-medium text-[var(--brand)]">查看基金详情 →</div>
        </button>
        <span className={actionBadgeClass(rec.action)}>{rec.action}</span>
      </div>
      {rec.suggested_amount_yuan != null ? (
        <p className="mt-2 break-words text-sm font-semibold text-[var(--brand-strong)] [overflow-wrap:anywhere]">
          示意金额 ¥{rec.suggested_amount_yuan.toLocaleString()}
          {rec.amount_note ? (
            <span className="ml-1 font-normal text-slate-500">（{translateEvidenceText(rec.amount_note)}）</span>
          ) : null}
        </p>
      ) : null}
      {rec.suggested_position_change_percent != null ? (
        <DiscoveryPositionChangeBadge
          percent={rec.suggested_position_change_percent}
          basis={rec.suggested_position_change_basis}
        />
      ) : null}
      {rec.points?.[0] ? (
        <p className="mt-3 break-words text-sm leading-6 text-slate-700 [overflow-wrap:anywhere]">
          <span className="font-black text-slate-900">核心理由：</span>
          {translateEvidenceText(rec.points[0])}
        </p>
      ) : null}
      {(rec.risks ?? []).length ? (
        <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {(rec.risks ?? []).map((risk) => (
            <div className="break-words [overflow-wrap:anywhere]" key={risk}>⚠ {translateEvidenceText(risk)}</div>
          ))}
        </div>
      ) : null}
      {rec.decision_path || rec.sector_evidence?.length || rec.fund_evidence?.length || rec.validation_notes?.length || (rec.points?.length ?? 0) > 1 ? (
        <details className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/60">
          <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-2 px-3 text-xs font-black text-slate-700 hover:bg-slate-100">
            查看决策路径与专业依据
            <ChevronDown size={16} className="text-slate-500" aria-hidden />
          </summary>
          <div className="space-y-3 border-t border-slate-200 p-3">
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
            {(rec.points?.length ?? 0) > 1 ? (
              <ul className="space-y-1 text-sm text-slate-700">
                {(rec.points ?? []).slice(1).map((point) => (
                  <li className="break-words [overflow-wrap:anywhere]" key={point}>· {translateEvidenceText(point)}</li>
                ))}
              </ul>
            ) : null}
          </div>
        </details>
      ) : null}
    </article>
  );
}

function RecommendationGroup({
  id,
  title,
  description,
  recommendations,
  onOpenFund,
}: {
  id: string;
  title: string;
  description: string;
  recommendations: DiscoveryRecommendation[];
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
}) {
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
        <span className="shrink-0 text-xs font-bold text-slate-500">{recommendations.length} 只</span>
      </div>
      {recommendations.map((rec) => (
        <DiscoveryRecommendationCard key={rec.fund_code} rec={rec} onOpenFund={onOpenFund} />
      ))}
    </section>
  );
}

export function DiscoveryReportPanel({ report, onOpenFund }: DiscoveryReportPanelProps) {
  const sectorOpportunities = report.discovery_facts?.sector_opportunities ?? [];
  const [chatOpen, setChatOpen] = useState(false);
  const chatDrawerId = `discovery-report-chat-${report.id}`;
  const groupedRecommendations = useMemo(() => {
    const actionable: DiscoveryRecommendation[] = [];
    const conditionalWait: DiscoveryRecommendation[] = [];
    const watchOnly: DiscoveryRecommendation[] = [];
    const decisionStatusByCode: Record<string, DiscoveryCandidateDecisionStatus> = {};

    for (const recommendation of report.recommendations) {
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

    return { actionable, conditionalWait, watchOnly, decisionStatusByCode };
  }, [report]);
  const selectedCodes = groupedRecommendations.actionable.map((item) => item.fund_code);
  const blockedCount = report.discovery_facts?.data_evidence_guard?.blocked_fund_codes?.length ?? 0;

  return (
    <div className="grid min-w-0 gap-4">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="font-display text-lg font-extrabold text-slate-950">{report.title}</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">{report.summary}</p>
        {report.market_view ? (
          <p className="mt-3 text-sm leading-6 text-slate-700">
            <span className="font-semibold text-slate-900">市场观点：</span>
            {report.market_view}
          </p>
        ) : null}
        {report.target_sectors?.length ? (
          <p className="mt-2 text-xs text-slate-500">
            扫描板块：{report.target_sectors.join("、")}
          </p>
        ) : null}
      </section>

      {sectorOpportunities.length ? (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-black text-slate-950">本次主方向</h3>
            <span className="text-xs font-medium text-slate-500">
              系统按近期涨跌、主力资金和资金动作预筛
            </span>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {sectorOpportunities.slice(0, 4).map((item) => (
              <SectorOpportunityCard key={`${item.sector_label}-${item.track ?? "track"}`} item={item} />
            ))}
          </div>
        </section>
      ) : null}

      <section
        data-testid="discovery-decision-summary"
        className={`rounded-2xl border px-4 py-3 shadow-sm ${
          groupedRecommendations.actionable.length
            ? "border-emerald-200 bg-emerald-50/80"
            : "border-amber-200 bg-amber-50/80"
        }`}
      >
        <h3 className="text-sm font-black text-slate-950">
          {groupedRecommendations.actionable.length
            ? `${groupedRecommendations.actionable.length} 只通过可执行校验`
            : "本次暂无可执行建议"}
        </h3>
        <p className="mt-1 text-xs leading-5 text-slate-700">
          {groupedRecommendations.actionable.length
            ? "仅下方“可执行建议”通过结构化动作、字段时点与基金质量校验，仍需由你最终确认。"
            : "当前候选未同时通过动作、字段时点与基金质量校验，不应据此直接买入。"}
          {blockedCount > 0 ? ` 其中 ${blockedCount} 只被字段级证据守卫降为观察。` : ""}
        </p>
      </section>

      <RecommendationGroup
        id="discovery-actionable-title"
        title="可执行建议"
        description="仅包含结构化决策为买入，且证据守卫与质量门均通过的候选。"
        recommendations={groupedRecommendations.actionable}
        onOpenFund={onOpenFund}
      />
      <RecommendationGroup
        id="discovery-conditional-title"
        title="等待条件"
        description="条件未满足前不执行，等待回调或下一次数据验证。"
        recommendations={groupedRecommendations.conditionalWait}
        onOpenFund={onOpenFund}
      />
      <RecommendationGroup
        id="discovery-watch-title"
        title="研究观察"
        description="仅保留研究线索，不构成买入建议，也不计入可执行推荐。"
        recommendations={groupedRecommendations.watchOnly}
        onOpenFund={onOpenFund}
      />

      <DiscoveryOutcomesPanel reportId={report.id} />

      {report.candidate_pool?.length ? (
        <DiscoveryCandidatePoolPanel
          pool={report.candidate_pool}
          selectedCodes={selectedCodes}
          decisionStatusByCode={groupedRecommendations.decisionStatusByCode}
          eliminatedCandidates={report.eliminated_candidates}
        />
      ) : null}

      {report.caveats?.length ? (
        <section className="rounded-xl border border-amber-100 bg-amber-50/80 px-4 py-3 text-xs leading-5 text-amber-900">
          {report.caveats.map((line) => (
            <p className="break-words [overflow-wrap:anywhere]" key={line}>{translateEvidenceText(line)}</p>
          ))}
        </section>
      ) : null}

      <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <button
          type="button"
          onClick={() => setChatOpen(true)}
          className="flex min-h-14 w-full items-center justify-between gap-3 px-4 text-left"
          aria-expanded={chatOpen}
          aria-controls={chatDrawerId}
          aria-haspopup="dialog"
        >
          <span className="flex items-center gap-2 text-sm font-black text-slate-900">
            <MessageCircle size={18} className="text-[var(--brand)]" aria-hidden />
            追问本次推荐
          </span>
          <span className="text-xs font-bold text-[var(--brand-strong)]" aria-hidden>
            打开追问面板
          </span>
        </button>
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

