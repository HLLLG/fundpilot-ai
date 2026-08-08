"use client";

import { useEffect, useState } from "react";
import { CheckCircle, Loader2, XCircle } from "lucide-react";
import type { Report } from "@/lib/api";
import { fetchAnalysisJob } from "@/lib/api";
import { userFacingErrorMessage } from "@/lib/userFacingError";
import { JobProgressCard } from "@/components/JobProgressCard";

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
          setError(userFacingErrorMessage(err, "分析失败，请重试。"));
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

  if (state === "completed") {
    return (
      <JobProgressCard
        tone="info"
        testId="analysis-job-float"
        icon={<CheckCircle size={18} className="text-[var(--success-icon)]" />}
        title="报告已生成"
        primaryAction={{
          label: "查看报告",
          onClick: () => {
            if (report) onComplete(report);
          },
        }}
        secondaryAction={{ label: "关闭", onClick: onClose }}
      />
    );
  }

  if (state === "failed") {
    return (
      <JobProgressCard
        tone="danger"
        testId="analysis-job-float"
        icon={<XCircle size={18} className="text-[var(--danger-fg)]" />}
        title="分析失败"
        detail={error ? <span className="line-clamp-2">{error}</span> : undefined}
        primaryAction={{ label: "重试", onClick: onRetry }}
        secondaryAction={{ label: "关闭", onClick: onClose }}
      />
    );
  }

  return (
    <JobProgressCard
      tone="info"
      testId="analysis-job-float"
      icon={<Loader2 size={18} className="animate-spin text-blue-600" />}
      title={stageLabel}
      detail={etaHint(analysisMode)}
    />
  );
}
