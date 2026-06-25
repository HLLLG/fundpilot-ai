"use client";

import { Loader2, X, Bell } from "lucide-react";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import { StatusPill } from "@/components/StatusPill";

type DiscoverySkeletonProps = {
  streaming: StreamingDiscoveryState;
  onCancel?: () => void;
};

function SkeletonCard({ fundCode, fundName }: { fundCode: string; fundName: string }) {
  return (
    <div
      className="animate-pulse rounded-xl border border-slate-200 bg-white/80 px-4 py-4"
      data-testid={`discovery-skeleton-${fundCode}`}
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

export function DiscoverySkeleton({ streaming, onCancel }: DiscoverySkeletonProps) {
  const { stageLabel, fundCodes, fundNames, title, summary, partialByCode, caveats, tokenBuffer, stage } =
    streaming;
  const showTypewriter = stage === "generating" && tokenBuffer.length > 0;

  return (
    <section
      className="section-card overflow-hidden"
      data-testid="discovery-streaming"
    >
      <div className="border-b border-[var(--line)] px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--brand-strong)]" />
            <span>{stageLabel || "AI 扫描中…"}</span>
          </div>
          {onCancel ? (
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-600 hover:border-slate-300"
              data-testid="discovery-stream-cancel-btn"
            >
              <X size={14} />
              停止扫描
            </button>
          ) : null}
        </div>
      </div>

      <div className="space-y-4 p-4 sm:p-5">
        <div className="flex items-start gap-2 rounded-xl border border-emerald-100 bg-emerald-50/70 px-3 py-2.5 text-xs leading-5 text-emerald-900">
          <Bell size={14} className="mt-0.5 shrink-0" />
          <span>可切换至其他页面继续操作；完成后将发送浏览器通知，发现 Tab 显示红点提醒。</span>
        </div>

        {title ? (
          <div>
            <StatusPill tone="blue">生成中</StatusPill>
            <h3 className="font-display mt-3 text-xl font-extrabold text-slate-950">{title}</h3>
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
                    className="rounded-xl border border-emerald-100 bg-emerald-50/50 px-4 py-3.5"
                    data-testid={`discovery-partial-${code}`}
                  >
                    <div className="text-sm font-black text-slate-950">
                      {code} · {partial.fund_name ?? fundNames[index] ?? code}
                    </div>
                    <div className="mt-1 text-xs font-bold text-emerald-800">{partial.action}</div>
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
          <div className="rounded-xl border border-amber-100 bg-amber-50/80 px-4 py-3 text-sm text-amber-900">
            {caveats.map((line, index) => (
              <p key={index}>{line}</p>
            ))}
          </div>
        ) : null}

        {showTypewriter ? (
          <div className="rounded-xl border border-slate-200 bg-slate-950/95 px-3 py-2.5">
            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-5 text-emerald-300">
              {tokenBuffer}
              <span className="animate-pulse text-emerald-400">▍</span>
            </pre>
          </div>
        ) : null}
      </div>
    </section>
  );
}
