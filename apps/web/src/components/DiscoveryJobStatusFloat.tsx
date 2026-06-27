"use client";

import { useEffect, useState } from "react";
import { CheckCircle, Loader2, XCircle } from "lucide-react";
import type { FundDiscoveryReport } from "@/lib/api";
import { fetchDiscoveryJob } from "@/lib/api";

type JobState = "running" | "completed" | "failed";

interface DiscoveryJobStatusFloatProps {
  jobId: string | null;
  onComplete: (report: FundDiscoveryReport) => void;
  onClose: () => void;
  onRetry: () => void;
}

export function DiscoveryJobStatusFloat({
  jobId,
  onComplete,
  onClose,
  onRetry,
}: DiscoveryJobStatusFloatProps) {
  const [state, setState] = useState<JobState>("running");
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<FundDiscoveryReport | null>(null);
  const [stageLabel, setStageLabel] = useState("正在扫描机会…");

  useEffect(() => {
    if (!jobId) return;
    setState("running");
    setError(null);
    setReport(null);
    setStageLabel("排队中…");

    let cancelled = false;
    let transientFailures = 0;
    const poll = async () => {
      while (!cancelled) {
        try {
          const job = await fetchDiscoveryJob(jobId);
          if (cancelled) return;
          if (job.transient_unavailable) {
            transientFailures += 1;
            if (transientFailures < 8) {
              setStageLabel(job.stage_label ?? "连接波动，正在重试...");
              await new Promise((resolve) => setTimeout(resolve, 2000));
              continue;
            }
            setError("数据库连接暂不可用，扫描任务可能仍在后台运行，请稍后查看历史记录。");
            setState("failed");
            return;
          }
          transientFailures = 0;
          if (job.stage_label) setStageLabel(job.stage_label);
          if (job.status === "completed" && job.discovery_report) {
            setReport(job.discovery_report);
            setState("completed");
            return;
          }
          if (job.status === "failed") {
            setError(job.error ?? "扫描失败，请重试。");
            setState("failed");
            return;
          }
        } catch (err: unknown) {
          if (cancelled) return;
          transientFailures += 1;
          if (transientFailures < 8) {
            setStageLabel("连接波动，正在重试…");
            await new Promise((resolve) => setTimeout(resolve, 2000));
            continue;
          }
          setError(err instanceof Error ? err.message : "扫描失败，请重试。");
          setState("failed");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    };
    void poll();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (!jobId) return null;

  return (
    <div className="w-full rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_32px_rgba(0,0,0,0.12)]">
      {state === "running" ? (
        <div className="flex items-start gap-3">
          <Loader2 size={20} className="mt-0.5 shrink-0 animate-spin text-[var(--brand)]" />
          <div>
            <div className="text-sm font-bold text-slate-900">{stageLabel}</div>
            <div className="mt-0.5 text-xs text-slate-500">可继续浏览页面</div>
          </div>
        </div>
      ) : null}

      {state === "completed" ? (
        <div>
          <div className="flex items-start gap-3">
            <CheckCircle size={20} className="mt-0.5 shrink-0 text-emerald-500" />
            <div className="text-sm font-bold text-slate-900">推荐报告已生成</div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (report) onComplete(report);
              }}
              className="flex-1 rounded-xl bg-[var(--brand)] px-3 py-2 text-xs font-bold text-white hover:bg-[var(--brand-strong)]"
            >
              查看报告
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
      ) : null}

      {state === "failed" ? (
        <div>
          <div className="flex items-start gap-3">
            <XCircle size={20} className="mt-0.5 shrink-0 text-red-500" />
            <div>
              <div className="text-sm font-bold text-slate-900">扫描失败</div>
              {error ? (
                <div className="mt-0.5 line-clamp-2 text-xs text-slate-500">{error}</div>
              ) : null}
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={onRetry}
              className="flex-1 rounded-xl bg-[var(--brand)] px-3 py-2 text-xs font-bold text-white hover:bg-[var(--brand-strong)]"
            >
              重试
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
