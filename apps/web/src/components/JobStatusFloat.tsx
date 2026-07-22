"use client";

import { useEffect, useState } from "react";
import { CheckCircle, Loader2, XCircle } from "lucide-react";
import type { Report } from "@/lib/api";
import { fetchAnalysisJob } from "@/lib/api";

type JobState = "running" | "completed" | "failed";

interface JobStatusFloatProps {
  jobId: string | null;
  onComplete: (report: Report) => void;
  onClose: () => void;
  onRetry: () => void;
}

function etaHint(analysisMode?: string) {
  return analysisMode === "deep"
    ? "深度模式预计 30 秒–3 分钟，可继续操作页面"
    : "快速模式预计 15–45 秒，可继续操作页面";
}

export function JobStatusFloat({ jobId, onComplete, onClose, onRetry }: JobStatusFloatProps) {
  const [state, setState] = useState<JobState>("running");
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [stageLabel, setStageLabel] = useState("正在生成报告…");
  const [analysisMode, setAnalysisMode] = useState<string>("deep");

  useEffect(() => {
    if (!jobId) {
      return;
    }
    setState("running");
    setError(null);
    setReport(null);
    setStageLabel("排队中…");

    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        try {
          const job = await fetchAnalysisJob(jobId);
          if (cancelled) return;

          if (job.analysis_mode) {
            setAnalysisMode(job.analysis_mode);
          }
          if (job.stage_label) {
            setStageLabel(job.stage_label);
          }

          if (job.status === "completed" && job.report) {
            setReport(job.report);
            setState("completed");
            return;
          }
          if (job.status === "failed") {
            setError(job.error ?? "分析失败，请重试。");
            setState("failed");
            return;
          }
        } catch (err: unknown) {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "分析失败，请重试。");
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

  if (!jobId) {
    return null;
  }

  return (
    <div className="w-full rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_32px_rgba(0,0,0,0.12)]">
      {state === "running" && (
        <div className="flex items-start gap-3">
          <Loader2 size={20} className="mt-0.5 shrink-0 animate-spin text-blue-600" />
          <div>
            <div className="text-sm font-bold text-slate-900">{stageLabel}</div>
            <div className="mt-0.5 text-xs text-slate-500">{etaHint(analysisMode)}</div>
          </div>
        </div>
      )}

      {state === "completed" && (
        <div>
          <div className="flex items-start gap-3">
            <CheckCircle size={20} className="mt-0.5 shrink-0 text-[var(--success-icon)]" />
            <div className="text-sm font-bold text-slate-900">报告已生成</div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (report) onComplete(report);
              }}
              className="min-h-11 flex-1 rounded-xl bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-700"
            >
              查看报告
            </button>
            <button
              type="button"
              onClick={onClose}
              className="min-h-11 rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
      )}

      {state === "failed" && (
        <div>
          <div className="flex items-start gap-3">
            <XCircle size={20} className="mt-0.5 shrink-0 text-[var(--danger-fg)]" />
            <div>
              <div className="text-sm font-bold text-slate-900">分析失败</div>
              {error && (
                <div className="mt-0.5 line-clamp-2 text-xs text-slate-500">{error}</div>
              )}
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={onRetry}
              className="min-h-11 flex-1 rounded-xl bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-700"
            >
              重试
            </button>
            <button
              type="button"
              onClick={onClose}
              className="min-h-11 rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
