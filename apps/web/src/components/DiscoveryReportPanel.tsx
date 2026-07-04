"use client";

import { TrendingDown, TrendingUp } from "lucide-react";
import type { DiscoveryRecommendation, FundDiscoveryReport } from "@/lib/api";
import { actionBadgeClass } from "@/lib/actionStyles";
import { translateEvidenceText } from "@/lib/decisionText";
import { DecisionEvidenceGrid } from "@/components/DecisionEvidenceGrid";
import { DiscoveryCandidatePoolPanel } from "@/components/DiscoveryCandidatePoolPanel";
import { DiscoveryChatPanel } from "@/components/DiscoveryChatPanel";
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

export function DiscoveryReportPanel({ report, onOpenFund }: DiscoveryReportPanelProps) {
  const selectedCodes = report.recommendations.map((item) => item.fund_code);
  const sectorOpportunities = report.discovery_facts?.sector_opportunities ?? [];

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

      {report.candidate_pool?.length ? (
        <DiscoveryCandidatePoolPanel
          pool={report.candidate_pool}
          selectedCodes={selectedCodes}
          eliminatedCandidates={report.eliminated_candidates}
        />
      ) : null}

      <section className="grid gap-3">
        {report.recommendations.map((rec) => (
          <article
            key={rec.fund_code}
            className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <button
                type="button"
                onClick={() => onOpenFund?.(rec)}
                className="min-w-0 text-left transition hover:text-[var(--brand-strong)]"
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
            {rec.decision_path ? (
              <div className="mt-3 rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2.5 text-sm leading-6 text-blue-950">
                <div className="text-xs font-black text-blue-900">决策路径</div>
                <p className="mt-1 break-words [overflow-wrap:anywhere]">{translateEvidenceText(rec.decision_path)}</p>
              </div>
            ) : null}
            <DecisionEvidenceGrid
              sectorEvidence={rec.sector_evidence}
              fundEvidence={rec.fund_evidence}
              validationNotes={rec.validation_notes}
            />
            <ul className="mt-3 space-y-1 text-sm text-slate-700">
              {(rec.points ?? []).map((point) => (
                <li className="break-words [overflow-wrap:anywhere]" key={point}>· {translateEvidenceText(point)}</li>
              ))}
            </ul>
            {(rec.risks ?? []).length ? (
              <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900">
                {(rec.risks ?? []).map((risk) => (
                  <div className="break-words [overflow-wrap:anywhere]" key={risk}>⚠ {translateEvidenceText(risk)}</div>
                ))}
              </div>
            ) : null}
          </article>
        ))}
      </section>

      <DiscoveryOutcomesPanel reportId={report.id} />

      {report.caveats?.length ? (
        <section className="rounded-xl border border-amber-100 bg-amber-50/80 px-4 py-3 text-xs leading-5 text-amber-900">
          {report.caveats.map((line) => (
            <p className="break-words [overflow-wrap:anywhere]" key={line}>{translateEvidenceText(line)}</p>
          ))}
        </section>
      ) : null}

      <DiscoveryChatPanel reportId={report.id} reportTitle={report.title} />
    </div>
  );
}

