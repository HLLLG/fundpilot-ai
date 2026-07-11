"use client";

import { useId, useState } from "react";
import { ChevronDown, Download, LoaderCircle } from "lucide-react";

import { StatusPill } from "@/components/StatusPill";
import type { Report } from "@/lib/api";
import { portfolioRecommendationLines } from "@/lib/reportPresentation";

type ReportSummaryHeroProps = {
  report: Report;
  needsActionCount: number;
  isExporting: boolean;
  onExport: () => void;
};

type MetricProps = {
  label: string;
  value: string;
  emphasis?: boolean;
};

const riskTone = { low: "green", medium: "amber", high: "red" } as const;
const riskLabel = { low: "较低", medium: "中等", high: "较高" } as const;
const actionLabel = {
  watch: "观察",
  pause_add: "暂停加仓",
  staggered_add: "分批加仓",
  risk_review: "减仓/风控复核",
} as const;

function Metric({ label, value, emphasis = false }: MetricProps) {
  return (
    <div
      className={`min-w-0 rounded-2xl border px-2 py-3 text-center sm:px-4 sm:py-3.5 ${
        emphasis
          ? "border-blue-200/80 bg-[var(--brand-soft)]"
          : "border-slate-100 bg-slate-50/80"
      }`}
    >
      <dt className="break-words text-[10px] font-bold leading-4 tracking-wide text-slate-500 sm:text-xs">
        {label}
      </dt>
      <dd
        className={`tnum mt-1 break-words font-display text-base font-extrabold leading-tight sm:text-xl ${
          emphasis ? "text-[var(--brand-deep)]" : "text-slate-900"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

export function ReportSummaryHero({
  report,
  needsActionCount,
  isExporting,
  onExport,
}: ReportSummaryHeroProps) {
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [portfolioOpen, setPortfolioOpen] = useState(false);
  const headingId = useId();
  const metadataId = useId();
  const portfolioId = useId();
  const portfolioLines = portfolioRecommendationLines(report);

  return (
    <section aria-labelledby={headingId} className="report-panel overflow-hidden p-4 sm:p-5">
      <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,25rem)] lg:items-start">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap gap-2">
            <StatusPill tone={riskTone[report.risk.level]}>
              风险 {riskLabel[report.risk.level]}
            </StatusPill>
            <StatusPill tone="dark">{actionLabel[report.risk.suggested_action]}</StatusPill>
          </div>
          <h2
            id={headingId}
            className="font-display text-2xl font-extrabold tracking-tight text-slate-950"
          >
            {report.title}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{report.summary}</p>
        </div>

        <dl
          className="grid min-w-0 grid-cols-3 gap-2"
          data-testid="report-summary-metrics"
        >
          <Metric
            label="组合收益"
            value={`${report.risk.weighted_return_percent}%`}
            emphasis
          />
          <Metric label="组合风险" value={riskLabel[report.risk.level]} />
          <Metric label="需要处理" value={`${needsActionCount} 只`} />
        </dl>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
        <div className="flex flex-wrap gap-1">
          {portfolioLines.length ? (
            <button
              type="button"
              aria-controls={portfolioId}
              aria-expanded={portfolioOpen}
              onClick={() => setPortfolioOpen((value) => !value)}
              className="inline-flex items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-bold text-slate-500 transition hover:bg-slate-50 hover:text-[var(--brand-strong)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]"
            >
              组合说明
              <ChevronDown
                aria-hidden="true"
                className={`size-3.5 transition-transform ${portfolioOpen ? "rotate-180" : ""}`}
              />
            </button>
          ) : null}
          <button
            type="button"
            aria-controls={metadataId}
            aria-expanded={metadataOpen}
            onClick={() => setMetadataOpen((value) => !value)}
            className="inline-flex items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-bold text-slate-500 transition hover:bg-slate-50 hover:text-[var(--brand-strong)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]"
          >
            报告信息
            <ChevronDown
              aria-hidden="true"
              className={`size-3.5 transition-transform ${metadataOpen ? "rotate-180" : ""}`}
            />
          </button>
        </div>

        <button
          type="button"
          aria-busy={isExporting}
          aria-label={isExporting ? "正在导出 Markdown" : "导出 Markdown"}
          onClick={onExport}
          disabled={isExporting}
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl bg-[var(--brand-deep)] px-4 py-2 text-sm font-bold text-white shadow-sm transition hover:bg-[var(--brand-strong)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] disabled:cursor-wait disabled:opacity-60"
        >
          {isExporting ? (
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          ) : (
            <Download aria-hidden="true" className="size-4" />
          )}
          {isExporting ? "导出中..." : "导出 Markdown"}
        </button>
      </div>

      {portfolioOpen ? (
        <ul
          id={portfolioId}
          data-testid="report-summary-portfolio"
          className="mt-3 space-y-1.5 rounded-2xl border border-blue-100 bg-blue-50/50 px-4 py-3 text-sm leading-6 text-slate-700"
        >
          {portfolioLines.map((line, index) => (
            <li key={`${index}-${line}`} className="pl-3 before:-ml-3 before:mr-2 before:text-blue-400 before:content-['•']">
              {line}
            </li>
          ))}
        </ul>
      ) : null}

      {metadataOpen ? (
        <div
          id={metadataId}
          data-testid="report-summary-metadata"
          className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 rounded-2xl border border-slate-100 bg-slate-50/80 px-4 py-3 text-xs leading-5 text-slate-500"
        >
          <span>
            模型 <span className="font-semibold text-slate-700">{report.provider}</span>
          </span>
          <span>
            生成时间{" "}
            <time className="font-semibold text-slate-700" dateTime={report.created_at}>
              {report.created_at}
            </time>
          </span>
        </div>
      ) : null}
    </section>
  );
}
