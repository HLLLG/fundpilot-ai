"use client";

import { useState } from "react";

import { ReportChatDrawer } from "@/components/ReportChatDrawer";
import { ReportDetailsHub } from "@/components/ReportDetailsHub";
import { ReportRecommendationList } from "@/components/ReportRecommendationList";
import { ReportSkeleton } from "@/components/ReportSkeleton";
import { ReportSummaryHero } from "@/components/ReportSummaryHero";
import { StatusPill } from "@/components/StatusPill";
import type { Holding, Report } from "@/lib/api";
import { fetchReportMarkdown } from "@/lib/api";
import {
  displayFundRecommendations,
  groupFundRecommendations,
  scopeReportToCurrentHoldings,
} from "@/lib/reportPresentation";

type ReportPanelProps = {
  report: Report | null;
  streaming?: import("@/lib/streamApi").StreamingReportState | null;
  onCancelStream?: () => void;
  onStreamFollowup?: (message: string) => Promise<void>;
  diagnostics?: () => React.ReactNode;
  currentHoldings?: Holding[];
  onConfirmLedgerBaseline?: () => void;
};

export function ReportPanel({
  report,
  streaming,
  onCancelStream,
  onStreamFollowup,
  diagnostics,
  currentHoldings,
  onConfirmLedgerBaseline,
}: ReportPanelProps) {
  const [isExporting, setIsExporting] = useState(false);

  if (streaming && !report) {
    return (
      <ReportSkeleton
        streaming={streaming}
        onCancel={onCancelStream}
        onFollowup={onStreamFollowup}
      />
    );
  }

  const handleExportMarkdown = async () => {
    if (!report) return;

    setIsExporting(true);
    try {
      const markdown = await fetchReportMarkdown(report.id);
      const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${report.title || "fund-report"}.md`;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setIsExporting(false);
    }
  };

  if (!report) {
    return (
      <section className="glass-panel signal-grid min-w-0 rounded-[28px] p-6">
        <div className="flex min-h-80 flex-col justify-between rounded-[24px] bg-white/75 p-6">
          <div>
            <StatusPill tone="blue">等待生成</StatusPill>
            <h2 className="font-display mt-5 text-2xl font-extrabold text-slate-950">
              你的日报会出现在这里
            </h2>
            <p className="mt-3 max-w-lg text-sm leading-6 text-slate-600">
              上传截图并确认持仓后，系统会先跑硬风控，再让 DeepSeek
              生成带风险边界的操作日报。
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            {["规则先行", "模型辅助", "人工确认"].map((item) => (
              <div
                key={item}
                className="rounded-2xl border border-slate-100 bg-white px-4 py-3 text-sm font-bold text-slate-700"
              >
                {item}
              </div>
            ))}
          </div>
        </div>
      </section>
    );
  }

  const scoped = scopeReportToCurrentHoldings(report, currentHoldings);
  const viewReport = scoped.report;
  const fundRecommendations = displayFundRecommendations(viewReport);
  const groups = groupFundRecommendations(fundRecommendations);

  return (
    <div className="report-workspace min-w-0" data-testid="report-workspace">
      <section
        className="report-shell min-w-0 space-y-4 animate-fade-up"
        data-testid="report-ready"
      >
        <ReportSummaryHero
          report={viewReport}
          needsActionCount={groups.needsAction.length}
          isExporting={isExporting}
          onExport={() => void handleExportMarkdown()}
        />
        {scoped.hiddenRecommendationCount > 0 ? (
          <p className="rounded-2xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
            持仓已更新，已自动隐藏 {scoped.hiddenRecommendationCount} 条不属于当前持仓的旧建议。
          </p>
        ) : null}
        <ReportRecommendationList
          report={viewReport}
          recommendations={fundRecommendations}
          onConfirmLedgerBaseline={onConfirmLedgerBaseline}
        />
        <ReportDetailsHub report={viewReport} diagnostics={diagnostics} />
      </section>
      <ReportChatDrawer reportId={viewReport.id} reportTitle={viewReport.title} />
    </div>
  );
}
