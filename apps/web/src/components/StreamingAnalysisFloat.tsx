"use client";

import { ExternalLink, Loader2, X } from "lucide-react";
import type { StreamingReportState } from "@/lib/streamApi";
import { stageShortLabel } from "@/lib/streamingStageMeta";

type StreamingAnalysisFloatProps = {
  streaming: StreamingReportState;
  onOpenReport: () => void;
  onCancel: () => void;
};

export function StreamingAnalysisFloat({
  streaming,
  onOpenReport,
  onCancel,
}: StreamingAnalysisFloatProps) {
  const filledCount = Object.values(streaming.partialByCode).filter((item) => item.action).length;
  const totalFunds = streaming.fundCodes.length;

  return (
    <div
      className="w-full rounded-2xl border border-blue-100 bg-white p-4 shadow-[0_8px_32px_rgba(37,99,235,0.12)]"
      data-testid="streaming-analysis-float"
    >
      <div className="flex items-start gap-3">
        <Loader2 size={20} className="mt-0.5 shrink-0 animate-spin text-blue-600" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-bold text-slate-900">{streaming.stageLabel}</div>
          <div className="mt-0.5 text-xs text-slate-500">
            {stageShortLabel(streaming.stage)}
            {totalFunds > 0 ? ` · 持仓 ${filledCount}/${totalFunds}` : null}
            {" · 可切换页面，完成后通知您"}
          </div>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100"
          aria-label="取消分析"
        >
          <X size={16} />
        </button>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={onOpenReport}
          className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-700"
        >
          <ExternalLink size={14} />
          查看进度
        </button>
      </div>
    </div>
  );
}
