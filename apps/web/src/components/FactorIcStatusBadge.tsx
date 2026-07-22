"use client";

import { fetchFactorIcStatus, type FactorIcStatus } from "@/lib/api";
import { useLazyAsyncResource } from "@/lib/useLazyAsyncResource";


function shortDate(value?: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? "");
  if (!match) return value ?? "";
  return `${Number(match[2])}月${Number(match[3])}日`;
}


function StatusLine({
  tone,
  children,
}: {
  tone: "loading" | "fresh" | "stale" | "muted" | "error";
  children: React.ReactNode;
}) {
  const tones = {
    loading: "text-[var(--muted)] [&>i]:bg-[var(--muted-soft)]",
    fresh:
      "text-[var(--muted)] [&>i]:bg-[var(--success-icon)] [&>i]:shadow-[0_0_0_3px_var(--success-bg)]",
    stale:
      "text-[var(--warn-fg)] [&>i]:bg-[var(--warn-icon)] [&>i]:shadow-[0_0_0_3px_var(--warn-bg)]",
    muted: "text-[var(--muted)] [&>i]:bg-[var(--muted-soft)]",
    error: "text-[var(--danger-fg)] [&>i]:bg-[var(--danger-icon)]",
  } as const;
  return (
    <span
      role="status"
      className={`inline-flex max-w-full items-center gap-1.5 text-[11px] font-medium leading-4 ${tones[tone]}`}
    >
      <i aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full" />
      <span>{children}</span>
    </span>
  );
}


export function FactorIcStatusBadge() {
  const { data: status, error } = useLazyAsyncResource<FactorIcStatus>({
    enabled: true,
    load: fetchFactorIcStatus,
    errorMessage: "IC 状态暂不可用",
  });

  if (error) {
    return <StatusLine tone="error">IC 状态暂不可用</StatusLine>;
  }
  if (!status) {
    return (
      <span className="animate-pulse">
        <StatusLine tone="loading">IC 回测加载中…</StatusLine>
      </span>
    );
  }
  if (!status.available) {
    return <StatusLine tone="muted">IC 回测数据未接入</StatusLine>;
  }
  if (status.stale) {
    return (
      <StatusLine tone="stale">
        IC 回测已超过{status.stale_after_days}天，系统将继续自动重试
      </StatusLine>
    );
  }
  if (status.upgrade_required) {
    return (
      <StatusLine tone="stale">
        IC 旧版：{shortDate(status.run_date)} · {status.universe_size ?? "—"}只 ·
        待升级至{status.expected_universe_size ?? 1500}只
      </StatusLine>
    );
  }
  const scope =
    status.cohort_mode === "point_in_time"
      ? status.point_in_time?.point_in_time_scope === "nav_observation_pit" &&
        status.point_in_time?.nav_revision_pit === true
        ? "完整PIT"
        : "成员PIT"
      : status.pit_upgrade?.state === "collecting"
        ? `PIT积累${status.pit_upgrade.effective_anchor_count ?? 0}锚点`
        : "当前存续样本";
  return (
    <StatusLine tone="fresh">
      IC：{shortDate(status.run_date)} · {status.universe_size ?? "—"}只 · {scope}
    </StatusLine>
  );
}
