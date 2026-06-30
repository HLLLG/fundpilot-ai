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
              系统按涨跌、主力资金与资金/价格 pattern 预筛
            </span>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {sectorOpportunities.slice(0, 4).map((item) => (
              <div
                key={`${item.sector_label}-${item.track ?? "track"}`}
                className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm font-bold text-slate-900">{item.sector_label}</div>
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-bold">
                    {item.track ? (
                      <span className="rounded-full bg-white px-2 py-0.5 text-slate-600 ring-1 ring-slate-200">
                        track={item.track}
                      </span>
                    ) : null}
                    {item.confidence ? (
                      <span className="rounded-full bg-blue-50 px-2 py-0.5 text-blue-800 ring-1 ring-blue-100">
                        {item.confidence}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-600">
                  <Metric label="机会分" value={formatMetric(item.score)} />
                  <Metric label="1d/5d" value={`${formatMetric(item.change_1d_percent)} / ${formatMetric(item.change_5d_percent)}%`} />
                  <Metric label="今日主力" value={`${formatMetric(item.today_main_force_net_yi)} 亿`} />
                  <Metric label="5日主力" value={`${formatMetric(item.cumulative_5d_net_yi)} 亿`} />
                </div>
                {item.pattern_label || item.entry_hint ? (
                  <p className="mt-2 text-xs leading-5 text-slate-500">
                    {item.pattern_label ? `pattern=${item.pattern_label}` : ""}
                    {item.pattern_label && item.entry_hint ? " · " : ""}
                    {item.entry_hint ?? ""}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

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
              <p className="mt-2 text-sm font-semibold text-[var(--brand-strong)]">
                示意金额 ¥{rec.suggested_amount_yuan.toLocaleString()}
                {rec.amount_note ? (
                  <span className="ml-1 font-normal text-slate-500">（{rec.amount_note}）</span>
                ) : null}
              </p>
            ) : null}
            {rec.decision_path ? (
              <div className="mt-3 rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2.5 text-sm leading-6 text-blue-950">
                <div className="text-xs font-black text-blue-900">决策路径</div>
                <p className="mt-1">{rec.decision_path}</p>
              </div>
            ) : null}
            <EvidenceGrid recommendation={rec} />
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

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white px-2 py-1.5 ring-1 ring-slate-100">
      <div className="text-[10px] font-bold text-slate-400">{label}</div>
      <div className="mt-0.5 font-semibold text-slate-800">{value}</div>
    </div>
  );
}

function EvidenceGrid({ recommendation }: { recommendation: DiscoveryRecommendation }) {
  const groups = [
    ["板块依据", recommendation.sector_evidence],
    ["基金依据", recommendation.fund_evidence],
    ["校验备注", recommendation.validation_notes],
  ] as const;
  if (!groups.some(([, items]) => items?.length)) {
    return null;
  }
  return (
    <div className="mt-3 grid gap-2 md:grid-cols-3">
      {groups.map(([title, items]) =>
        items?.length ? (
          <div
            key={title}
            className={
              title === "校验备注"
                ? "rounded-xl border border-amber-100 bg-amber-50/70 px-3 py-2.5"
                : "rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2.5"
            }
          >
            <div className={title === "校验备注" ? "text-xs font-black text-amber-900" : "text-xs font-black text-slate-800"}>
              {title}
            </div>
            <ul className={title === "校验备注" ? "mt-1.5 space-y-1 text-xs leading-5 text-amber-900" : "mt-1.5 space-y-1 text-xs leading-5 text-slate-600"}>
              {items.slice(0, 3).map((item) => (
                <li key={item}>· {item}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </div>
  );
}

function formatMetric(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return Number(value).toFixed(2).replace(/\.00$/, "");
}
