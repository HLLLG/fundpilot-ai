"use client";

import { Bell, Loader2, X } from "lucide-react";
import type { StreamingReportState } from "@/lib/streamApi";
import { ReportThinkingSidebar } from "@/components/ReportThinkingSidebar";
import { StatusPill } from "@/components/StatusPill";

type ReportSkeletonProps = {
  streaming: StreamingReportState;
  onCancel?: () => void;
  onFollowup?: (message: string) => Promise<void>;
};

function SkeletonCard({ fundCode, fundName }: { fundCode: string; fundName: string }) {
  return (
    <div
      className="animate-pulse rounded-xl border border-slate-200 bg-white/80 px-4 py-4"
      data-testid={`report-skeleton-${fundCode}`}
    >
      <div className="flex items-center gap-2">
        <div className="h-4 w-24 rounded bg-slate-200" />
        <div className="h-5 w-16 rounded-full bg-slate-100" />
      </div>
      <p className="mt-2 text-xs text-slate-500">正在分析 {fundName || fundCode}…</p>
      <div className="mt-3 space-y-2">
        <div className="h-3 w-full rounded bg-slate-100" />
        <div className="h-3 w-4/5 rounded bg-slate-100" />
      </div>
    </div>
  );
}

export function ReportSkeleton({ streaming, onCancel, onFollowup }: ReportSkeletonProps) {
  const { stageLabel, fundCodes, fundNames, title, summary, partialByCode, caveats } = streaming;
  const showBackgroundFallbackFrame =
    Boolean(streaming.backgroundJobId) &&
    !title &&
    !summary &&
    fundCodes.length === 0 &&
    !caveats?.length;

  return (
    <section className="glass-panel signal-grid min-w-0 rounded-[28px] p-6" data-testid="report-streaming">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px]">
        <div className="space-y-4 rounded-[24px] bg-white/75 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm text-slate-600">
              <Loader2 className="h-4 w-4 animate-spin text-[var(--brand-strong)]" />
              <span>{stageLabel || "AI 分析中…"}</span>
            </div>
            {onCancel ? (
              <button
                type="button"
                onClick={onCancel}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-600 hover:border-slate-300"
                data-testid="stream-cancel-btn"
              >
                <X size={14} />
                停止生成
              </button>
            ) : null}
          </div>

          <div className="flex items-start gap-2 rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)]/80 px-3 py-2.5 text-xs leading-5 text-[var(--info-fg)]">
            <Bell size={14} className="mt-0.5 shrink-0" />
            <span>可切换至其他页面继续操作；完成后将发送浏览器通知，日报 Tab 显示红点提醒。</span>
          </div>

          {title ? (
            <div>
              <StatusPill tone="blue">生成中</StatusPill>
              <h2 className="font-display mt-3 text-2xl font-extrabold text-slate-950">{title}</h2>
            </div>
          ) : null}
          {summary ? <p className="text-sm leading-6 text-slate-600">{summary}</p> : null}

          {fundCodes.length > 0 ? (
            <div className="space-y-3">
              {fundCodes.map((code, index) => {
                const partial = partialByCode[code];
                if (partial?.action) {
                  return (
                    <div
                      key={code}
                      className="rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)]/80 px-4 py-3.5"
                      data-testid={`report-partial-${code}`}
                    >
                      <div className="text-sm font-black text-slate-950">
                        {code} · {partial.fund_name ?? fundNames[index] ?? code}
                      </div>
                      <div className="mt-1 text-xs font-bold text-[var(--info-fg)]">{partial.action}</div>
                      {partial.points?.length ? (
                        <ul className="mt-2 space-y-1 text-sm text-slate-700">
                          {partial.points.map((point, pointIndex) => (
                            <li key={pointIndex} className="list-disc pl-5">
                              {point}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  );
                }
                return (
                  <SkeletonCard
                    key={code}
                    fundCode={code}
                    fundName={fundNames[index] ?? code}
                  />
                );
              })}
            </div>
          ) : null}

          {caveats?.length ? (
            <div className="rounded-xl border border-[var(--warn-border)] bg-[var(--warn-bg)]/80 px-4 py-3 text-sm text-[var(--warn-fg)]">
              {caveats.map((line, index) => (
                <p key={index}>{line}</p>
              ))}
            </div>
          ) : null}

          {showBackgroundFallbackFrame ? (
            <div
              className="rounded-xl border border-slate-200 bg-white/80 px-4 py-4"
              data-testid="report-background-fallback-frame"
            >
              <div className="flex items-center gap-2">
                <div className="h-4 w-28 rounded bg-slate-200" />
                <div className="h-5 w-20 rounded-full bg-[var(--info-bg)]" />
              </div>
              <p className="mt-2 text-xs text-slate-500">
                后台任务正在继续生成日报，完成后会自动展示报告。
              </p>
              <div className="mt-3 space-y-2">
                <div className="h-3 w-full rounded bg-slate-100" />
                <div className="h-3 w-5/6 rounded bg-slate-100" />
                <div className="h-3 w-2/3 rounded bg-slate-100" />
              </div>
            </div>
          ) : null}
        </div>

        <ReportThinkingSidebar streaming={streaming} onFollowup={onFollowup} />
      </div>
    </section>
  );
}
