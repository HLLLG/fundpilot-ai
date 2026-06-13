"use client";

import type { FundDiscoveryReport } from "@/lib/api";
import { actionBadgeClass } from "@/lib/actionStyles";
import { DiscoveryChatPanel } from "@/components/DiscoveryChatPanel";

type DiscoveryReportPanelProps = {
  report: FundDiscoveryReport;
};

export function DiscoveryReportPanel({ report }: DiscoveryReportPanelProps) {
  return (
    <div className="grid min-w-0 gap-4">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-lg font-black text-slate-950">{report.title}</h2>
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

      <section className="grid gap-3">
        {report.recommendations.map((rec) => (
          <article
            key={rec.fund_code}
            className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="text-sm font-bold text-slate-900">
                  [{rec.fund_code}] {rec.fund_name}
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  {rec.sector_name}
                  {rec.hold_horizon ? ` · 持有期 ${rec.hold_horizon}` : ""}
                  {rec.confidence ? ` · 置信度 ${rec.confidence}` : ""}
                </div>
              </div>
              <span className={actionBadgeClass(rec.action)}>{rec.action}</span>
            </div>
            {rec.suggested_amount_yuan != null ? (
              <p className="mt-2 text-sm font-semibold text-indigo-700">
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
