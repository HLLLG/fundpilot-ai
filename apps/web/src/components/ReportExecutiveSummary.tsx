"use client";

import { ListChecks } from "lucide-react";
import type { Report } from "@/lib/api";
import { buildExecutiveSummary } from "@/lib/reportExecutiveSummary";

type ReportExecutiveSummaryProps = {
  report: Report;
};

export function ReportExecutiveSummary({ report }: ReportExecutiveSummaryProps) {
  const [portfolioLine, riskLine, fundLine] = buildExecutiveSummary(report);

  return (
    <div
      className="mb-6 rounded-[24px] border border-blue-200 bg-gradient-to-br from-blue-600 to-indigo-700 p-5 text-white shadow-[0_18px_40px_rgba(37,99,235,0.28)]"
      data-testid="report-executive-summary"
    >
      <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-blue-100">
        <ListChecks size={16} />
        今日三行结论
      </div>
      <ol className="space-y-2 text-sm font-semibold leading-6">
        <li className="flex gap-2">
          <span className="shrink-0 text-blue-200">1.</span>
          <span>{portfolioLine}</span>
        </li>
        <li className="flex gap-2">
          <span className="shrink-0 text-blue-200">2.</span>
          <span>{riskLine}</span>
        </li>
        <li className="flex gap-2">
          <span className="shrink-0 text-blue-200">3.</span>
          <span>{fundLine}</span>
        </li>
      </ol>
    </div>
  );
}
