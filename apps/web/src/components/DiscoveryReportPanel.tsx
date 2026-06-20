"use client";

import type { DiscoveryRecommendation, FundDiscoveryReport } from "@/lib/api";
import { actionBadgeClass } from "@/lib/actionStyles";
import { DiscoveryCandidatePoolPanel } from "@/components/DiscoveryCandidatePoolPanel";
import { DiscoveryChatPanel } from "@/components/DiscoveryChatPanel";
import { DiscoveryOutcomesPanel } from "@/components/DiscoveryOutcomesPanel";

type DiscoveryReportPanelProps = {
  report: FundDiscoveryReport;
  onOpenFund?: (recommendation: DiscoveryRecommendation) => void;
};

export function DiscoveryReportPanel({ report, onOpenFund }: DiscoveryReportPanelProps) {
  const selectedCodes = report.recommendations.map((item) => item.fund_code);

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

      {report.candidate_pool?.length ? (
        <DiscoveryCandidatePoolPanel pool={report.candidate_pool} selectedCodes={selectedCodes} />
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
                <div className="text-sm font-bold text-slate-900">
                  [{rec.fund_code}] {rec.fund_name}
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  {rec.sector_name}
                  {rec.hold_horizon ? ` · 持有期 ${rec.hold_horizon}` : ""}
                  {rec.confidence ? ` · 置信度 ${rec.confidence}` : ""}
                </div>
                <div className="mt-1 text-[11px] font-medium text-[var(--brand)]">查看基金详情 →</div>
              </button>
              <span className={actionBadgeClass(rec.action)}>{rec.action}</span>
            </div>
            {rec.suggested_amount_yuan != null ? (
              <p className="mt-2 text-sm font-semibold text-[var(--brand-strong)]">
                示意金额 ¥{rec.suggested_amount_yuan.toLocaleString()}
                {rec.amount_note ? (
                  <span className="ml-1 font-normal text-slate-500">（{rec.amount_note}）</span>
                ) : null}
              </p>
            ) : null}
            <ul className="mt-3 space-y-1 text-sm text-slate-700">
              {(rec.points ?? []).map((point) => (
                <li key={point}>· {point}</li>
              ))}
            </ul>
            {(rec.risks ?? []).length ? (
              <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900">
                {(rec.risks ?? []).map((risk) => (
                  <div key={risk}>⚠ {risk}</div>
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
            <p key={line}>{line}</p>
          ))}
        </section>
      ) : null}

      <DiscoveryChatPanel reportId={report.id} reportTitle={report.title} />
    </div>
  );
}
