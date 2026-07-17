"use client";

import { FlaskConical } from "lucide-react";

import type { DiscoveryQuantPreview } from "@/lib/api";

function formatYuan(value: number): string {
  return `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function signedPercent(value: number): string {
  if (!value) return "0%";
  return `${value > 0 ? "+" : ""}${Number(value.toFixed(2))}%`;
}

export function DiscoveryQuantPreviewBadge({ preview }: { preview?: DiscoveryQuantPreview | null }) {
  if (!preview || preview.status !== "eligible") return null;
  const applied = preview.application_status === "applied";
  const stateLabel = applied ? "已应用" : preview.mode === "shadow" ? "影子" : "未调整";
  return (
    <span className={`rounded-full px-2 py-1 text-[10px] font-black ring-1 ${
      applied
        ? "bg-cyan-50 text-cyan-900 ring-cyan-200"
        : "bg-violet-50 text-violet-900 ring-violet-200"
    }`}>
      量化试运行 · {stateLabel}
    </span>
  );
}

export function DiscoveryQuantPreviewCard({
  preview,
  compact = false,
}: {
  preview?: DiscoveryQuantPreview | null;
  compact?: boolean;
}) {
  if (!preview || preview.status !== "eligible") return null;
  const applied = preview.application_status === "applied";
  const shadow = preview.application_status === "shadow_only";
  const proposed = preview.proposed_adjustment_percent ?? 0;
  const appliedPercent = preview.applied_adjustment_percent ?? 0;
  const rank = preview.sector_rank && preview.sector_sample_size
    ? `板块候选 ${preview.sector_rank}/${preview.sector_sample_size}`
    : null;
  const hasAmountProjection = preview.base_amount_yuan != null && preview.projected_amount_yuan != null;
  const summary = applied && preview.adjusted_amount_yuan != null && preview.base_amount_yuan != null
    ? `首批 ${formatYuan(preview.base_amount_yuan)} → ${formatYuan(preview.adjusted_amount_yuan)}（${signedPercent(appliedPercent)}）`
    : shadow && hasAmountProjection
      ? `影子测算 ${formatYuan(preview.base_amount_yuan as number)} → ${formatYuan(preview.projected_amount_yuan as number)}（${signedPercent(proposed)}），本次未生效`
      : `同类因子分 ${preview.preview_score ?? "—"}，当前不改变动作`;

  return (
    <div
      aria-label="量化试运行依据"
      className={`mt-2 rounded-xl border border-violet-200 bg-violet-50/70 ${compact ? "px-2.5 py-2" : "px-3 py-2.5"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-1.5">
        <span className="flex items-center gap-1.5 text-[11px] font-black text-violet-950">
          <FlaskConical size={13} aria-hidden="true" />
          量化试运行
        </span>
        <span className="text-[10px] font-bold text-violet-700">
          {[rank, preview.data_as_of ? `数据 ${preview.data_as_of}` : null].filter(Boolean).join(" · ")}
        </span>
      </div>
      <p className="mt-1 text-[11px] font-semibold leading-5 text-violet-950">{summary}</p>
      <p className="mt-0.5 text-[10px] leading-4 text-violet-700">
        {compact
          ? "存续样本 · 非正式 PIT v3 · 不改变动作"
          : "当前存续样本，含幸存者偏差；非正式 PIT v3，不能升级买入动作或突破风险上限。"}
      </p>
    </div>
  );
}
