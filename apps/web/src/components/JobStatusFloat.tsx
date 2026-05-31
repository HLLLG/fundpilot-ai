"use client";

import { useEffect, useState } from "react";
import { CheckCircle, Loader2, XCircle } from "lucide-react";
import type { Report } from "@/lib/api";
import { waitForAnalysisJob } from "@/lib/api";

type JobState = "running" | "completed" | "failed";

interface JobStatusFloatProps {
  jobId: string | null;
  onComplete: (report: Report) => void;
  onClose: () => void;
  onRetry: () => void;
}

export function JobStatusFloat({ jobId, onComplete, onClose, onRetry }: JobStatusFloatProps) {
  const [state, setState] = useState<JobState>("running");
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    setState("running");
    setError(null);
    setReport(null);

    let cancelled = false;
    waitForAnalysisJob(jobId)
      .then((result) => {
        if (cancelled) return;
        setReport(result);
        setState("completed");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "分析失败，请重试。");
        setState("failed");
      });

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (!jobId) {
    return null;
  }

  return (
    <div className="fixed bottom-6 right-6 z-50 w-72 rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_32px_rgba(0,0,0,0.12)]">
      {state === "running" && (
        <div className="flex items-start gap-3">
          <Loader2 size={20} className="mt-0.5 shrink-0 animate-spin text-blue-600" />
          <div>
            <div className="text-sm font-bold text-slate-900">正在生成报告…</div>
            <div className="mt-0.5 text-xs text-slate-500">预计 10–30 秒，可继续操作页面</div>
          </div>
        </div>
      )}

      {state === "completed" && (
        <div>
          <div className="flex items-start gap-3">
            <CheckCircle size={20} className="mt-0.5 shrink-0 text-emerald-500" />
            <div className="text-sm font-bold text-slate-900">报告已生成</div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (report) onComplete(report);
              }}
              className="flex-1 rounded-xl bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-700"
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
      )}

      {state === "failed" && (
        <div>
          <div className="flex items-start gap-3">
            <XCircle size={20} className="mt-0.5 shrink-0 text-red-500" />
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
              className="flex-1 rounded-xl bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-700"
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
      )}
    </div>
  );
}
