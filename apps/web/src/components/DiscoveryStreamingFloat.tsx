"use client";

import { ExternalLink, Loader2 } from "lucide-react";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import { discoveryStageShortLabel } from "@/lib/discoveryStreamingStageMeta";
import { JobProgressCard } from "@/components/JobProgressCard";

type DiscoveryStreamingFloatProps = {
  streaming: StreamingDiscoveryState;
  onOpenDiscovery: () => void;
  onCancel: () => void;
};

export function DiscoveryStreamingFloat({
  streaming,
  onOpenDiscovery,
  onCancel,
}: DiscoveryStreamingFloatProps) {
  const filledCount = Object.values(streaming.partialByCode).filter((item) => item.action).length;
  const totalFunds = streaming.fundCodes.length;

  return (
    <JobProgressCard
      tone="success"
      testId="discovery-streaming-float"
      icon={<Loader2 size={18} className="animate-spin text-[var(--success-icon)]" />}
      title={streaming.stageLabel}
      detail={
        <>
          {discoveryStageShortLabel(streaming.stage)}
          {totalFunds > 0 ? ` · 候选 ${filledCount}/${totalFunds}` : null}
          {" · 可切换页面，完成后通知您"}
        </>
      }
      primaryAction={{
        label: "查看进度",
        icon: <ExternalLink size={14} />,
        onClick: onOpenDiscovery,
      }}
      onDismiss={onCancel}
      dismissLabel="取消扫描"
    />
  );
}
