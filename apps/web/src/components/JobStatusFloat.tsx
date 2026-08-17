"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, XCircle } from "lucide-react";
import type { Report } from "@/lib/api";
import { fetchAnalysisJob } from "@/lib/api";
import { userFacingErrorMessage } from "@/lib/userFacingError";
import { JobProgressCard } from "@/components/JobProgressCard";

type JobState = "running" | "failed";

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
  const [stageLabel, setStageLabel] = useState("正在生成报告…");
  const [analysisMode, setAnalysisMode] = useState<string>("deep");
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    setState("running");
    setError(null);
    setStageLabel("排队中…");

    let cancelled = false;
    let transientFailures = 0;
    const poll = async () => {
      while (!cancelled) {
        try {
          const job = await fetchAnalysisJob(jobId);
          if (cancelled) return;

          if (job.transient_unavailable) {
            transientFailures += 1;
            if (transientFailures < 8) {
              setStageLabel(job.stage_label ?? "连接波动，正在重试…");
              await new Promise((resolve) => setTimeout(resolve, 2000));
              continue;
            }
            setError("数据库连接暂不可用，分析任务可能仍在后台运行，请稍后查看历史记录。");
            setState("failed");
            return;
          }
          transientFailures = 0;

          if (job.analysis_mode) {
            setAnalysisMode(job.analysis_mode);
          }
          if (job.stage_label) {
            setStageLabel(job.stage_label);
          }

          if (job.status === "completed" && job.report) {
            onCompleteRef.current(job.report);
            return;
          }
          if (job.status === "failed") {
            setError(job.error ?? "分析失败，请重试。");
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
