"use client";

import { useEffect, useState } from "react";

import { fetchFactorIcStatus, type FactorIcStatus } from "@/lib/api";


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
    loading: "text-slate-400 [&>i]:bg-slate-300",
    fresh:
      "text-slate-500 [&>i]:bg-emerald-500 [&>i]:shadow-[0_0_0_3px_rgba(16,185,129,0.10)]",
    stale:
      "text-amber-700 [&>i]:bg-amber-500 [&>i]:shadow-[0_0_0_3px_rgba(245,158,11,0.10)]",
    muted: "text-slate-500 [&>i]:bg-slate-300",
    error: "text-rose-600 [&>i]:bg-rose-500",
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
  const [status, setStatus] = useState<FactorIcStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchFactorIcStatus()
      .then((result) => {
        if (!cancelled) setStatus(result);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
    return <StatusLine tone="muted">IC 回测暂未生成</StatusLine>;
  }
  if (status.stale) {
    return (
      <StatusLine tone="stale">
        IC 回测已超过{status.stale_after_days}天，系统将继续自动重试
      </StatusLine>
    );
  }
  return (
    <StatusLine tone="fresh">
      IC 回测：{shortDate(status.run_date)} · {status.universe_size ?? "—"}只基金
    </StatusLine>
  );
}
