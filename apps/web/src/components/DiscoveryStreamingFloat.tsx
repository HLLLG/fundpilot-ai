"use client";

import { ExternalLink, Loader2, X } from "lucide-react";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import { discoveryStageShortLabel } from "@/lib/discoveryStreamingStageMeta";

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
    <div
      className="w-full rounded-2xl border border-emerald-100 bg-white p-4 shadow-[0_8px_32px_rgba(16,185,129,0.12)]"
      data-testid="discovery-streaming-float"
    >
      <div className="flex items-start gap-3">
        <Loader2 size={20} className="mt-0.5 shrink-0 animate-spin text-emerald-700" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-bold text-slate-900">{streaming.stageLabel}</div>
          <div className="mt-0.5 text-xs text-slate-500">
            {discoveryStageShortLabel(streaming.stage)}
            {totalFunds > 0 ? ` · 候选 ${filledCount}/${totalFunds}` : null}
            {" · 可切换页面，完成后通知您"}
          </div>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100"
          aria-label="取消扫描"
        >
          <X size={16} />
        </button>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={onOpenDiscovery}
          className="inline-flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-xl bg-emerald-700 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-800"
        >
          <ExternalLink size={14} />
          查看进度
        </button>
      </div>
    </div>
  );
}
