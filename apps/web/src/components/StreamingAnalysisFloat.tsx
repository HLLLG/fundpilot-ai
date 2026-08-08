"use client";

import { ExternalLink, Loader2 } from "lucide-react";
import type { StreamingReportState } from "@/lib/streamApi";
import { stageShortLabel } from "@/lib/streamingStageMeta";
import { JobProgressCard } from "@/components/JobProgressCard";

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
    <JobProgressCard
      tone="info"
      testId="streaming-analysis-float"
      icon={<Loader2 size={18} className="animate-spin text-blue-600" />}
      title={streaming.stageLabel}
      detail={
        <>
          {stageShortLabel(streaming.stage)}
          {totalFunds > 0 ? ` · 持仓 ${filledCount}/${totalFunds}` : null}
          {" · 可切换页面，完成后通知您"}
        </>
      }
      primaryAction={{
        label: "查看进度",
        icon: <ExternalLink size={14} />,
        onClick: onOpenReport,
      }}
      onDismiss={onCancel}
      dismissLabel="取消分析"
    />
  );
}
