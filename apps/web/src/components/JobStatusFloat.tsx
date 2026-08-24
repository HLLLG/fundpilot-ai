"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, XCircle } from "lucide-react";
import type { Report } from "@/lib/api";
import { fetchAnalysisJob, fetchReportDetail } from "@/lib/api";
import type { AnalysisScanProgress } from "@/lib/analysisScanProgress";
import { userFacingErrorMessage } from "@/lib/userFacingError";
import { JobProgressCard } from "@/components/JobProgressCard";

type JobState = "running" | "failed";

interface JobStatusFloatProps {
  jobId: string | null;
  onComplete: (report: Report) => void;
  onClose: () => void;
  onRetry: () => void;
  onProgress?: (progress: AnalysisScanProgress) => void;
  hideCard?: boolean;
}

function etaHint(analysisMode?: string) {
  return analysisMode === "deep"
    ? "深度模式预计 30 秒–3 分钟，可继续操作页面"
    : "快速模式预计 15–45 秒，可继续操作页面";
}

export function JobStatusFloat({
  jobId,
  onComplete,
  onClose,
  onRetry,
  onProgress,
  hideCard = false,
}: JobStatusFloatProps) {
  const [state, setState] = useState<JobState>("running");
  const [error, setError] = useState<string | null>(null);
  const [stageLabel, setStageLabel] = useState("正在生成报告…");
  const [analysisMode, setAnalysisMode] = useState<string>("deep");
  const onCompleteRef = useRef(onComplete);
  const onProgressRef = useRef(onProgress);
  const lastStageRef = useRef("queued");

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  useEffect(() => {
    onProgressRef.current = onProgress;
  }, [onProgress]);

  useEffect(() => {
    if (!jobId) return;
    setState("running");
    setError(null);
    setStageLabel("排队中…");
    lastStageRef.current = "queued";
    onProgressRef.current?.({
      stage: "queued",
      stageLabel: "排队中…",
      status: "running",
    });

    let cancelled = false;
    let transientFailures = 0;

    const emit = (progress: AnalysisScanProgress) => {
      if (progress.stage) lastStageRef.current = progress.stage;
      if (progress.stageLabel) setStageLabel(progress.stageLabel);
      onProgressRef.current?.(progress);
    };

    const fail = (message: string, stage?: string | null) => {
      const nextStage = stage || lastStageRef.current;
      lastStageRef.current = nextStage;
      setError(message);
      setState("failed");
      emit({
        stage: nextStage,
        stageLabel: message,
        status: "failed",
        error: message,
      });
    };

    const poll = async () => {
      while (!cancelled) {
        try {
          const job = await fetchAnalysisJob(jobId);
          if (cancelled) return;
          if (job.analysis_mode) setAnalysisMode(job.analysis_mode);
          if (job.transient_unavailable) {
            transientFailures += 1;
            if (transientFailures < 8) {
              emit({
                stage: lastStageRef.current,
                stageLabel: job.stage_label ?? "连接波动，正在重试…",
                status: "running",
              });
              await new Promise((resolve) => setTimeout(resolve, 2000));
              continue;
            }
            fail("数据库连接暂不可用，分析任务可能仍在后台运行，请稍后查看历史记录。");
            return;
          }
          transientFailures = 0;
          const stage = job.stage || lastStageRef.current;
          if (job.status === "completed") {
            emit({
              stage: "completed",
              stageLabel: job.stage_label ?? "完成",
              status: "completed",
            });
            const reportId = job.report_id ?? job.report?.id;
            if (!reportId) {
              fail("日报已完成，但没有返回报告编号。", stage);
              return;
            }
            const report = await fetchReportDetail(reportId);
            if (cancelled) return;
            onCompleteRef.current(report);
            return;
          }
          if (job.status === "failed") {
            fail(job.error ?? "分析失败，请重试。", stage);
            return;
          }
          emit({
            stage,
            stageLabel: job.stage_label ?? "正在生成报告…",
            status: "running",
          });
        } catch (err: unknown) {
          if (cancelled) return;
          transientFailures += 1;
          if (transientFailures < 8) {
            emit({
              stage: lastStageRef.current,
              stageLabel: "连接波动，正在重试…",
              status: "running",
            });
            await new Promise((resolve) => setTimeout(resolve, 2000));
            continue;
          }
          fail(userFacingErrorMessage(err, "分析失败，请重试。"));
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

  if (!jobId || hideCard) return null;

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
