"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, XCircle } from "lucide-react";
import type { FundDiscoveryReport } from "@/lib/api";
import { fetchDiscoveryJob, fetchDiscoveryReportDetail } from "@/lib/api";
import type { DiscoveryScanProgress } from "@/lib/discoveryScanProgress";
import { userFacingErrorMessage } from "@/lib/userFacingError";
import { JobProgressCard } from "@/components/JobProgressCard";

type JobState = "running" | "failed";

interface DiscoveryJobStatusFloatProps {
  jobId: string | null;
  onComplete: (report: FundDiscoveryReport) => void;
  onClose: () => void;
  onRetry: () => void;
  onProgress?: (progress: DiscoveryScanProgress) => void;
  /** 发现页已有整条航线时，只保留轮询，不再叠一张小浮层。 */
  hideCard?: boolean;
}

export function DiscoveryJobStatusFloat({
  jobId,
  onComplete,
  onClose,
  onRetry,
  onProgress,
  hideCard = false,
}: DiscoveryJobStatusFloatProps) {
  const [state, setState] = useState<JobState>("running");
  const [error, setError] = useState<string | null>(null);
  const [stageLabel, setStageLabel] = useState("正在扫描机会…");
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

    const emit = (progress: DiscoveryScanProgress) => {
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
          const job = await fetchDiscoveryJob(jobId);
          if (cancelled) return;
          if (job.transient_unavailable) {
            transientFailures += 1;
            if (transientFailures < 8) {
              emit({
                stage: lastStageRef.current,
                stageLabel: job.stage_label ?? "连接波动，正在重试...",
                status: "running",
              });
              await new Promise((resolve) => setTimeout(resolve, 2000));
              continue;
            }
            fail("数据库连接暂不可用，扫描任务可能仍在后台运行，请稍后查看历史记录。");
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
            const reportId = job.discovery_report_id ?? job.discovery_report?.id;
            if (!reportId) {
              fail("扫描已完成，但没有返回报告编号。", stage);
              return;
            }
            const report = await fetchDiscoveryReportDetail(reportId);
            if (cancelled) return;
            onCompleteRef.current(report);
            return;
          }
          if (job.status === "failed") {
            fail(job.error ?? "扫描失败，请重试。", stage);
            return;
          }
          emit({
            stage,
            stageLabel: job.stage_label ?? "正在扫描机会…",
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
          fail(userFacingErrorMessage(err, "扫描失败，请重试。"));
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
        testId="discovery-job-float"
        icon={<XCircle size={18} className="text-[var(--danger-fg)]" />}
        title="扫描失败"
        detail={error ? <span className="line-clamp-2">{error}</span> : undefined}
        primaryAction={{ label: "重试", onClick: onRetry }}
        secondaryAction={{ label: "关闭", onClick: onClose }}
      />
    );
  }

  return (
    <JobProgressCard
      tone="neutral"
      testId="discovery-job-float"
      icon={<Loader2 size={18} className="animate-spin text-[var(--brand)]" />}
      title={stageLabel}
      detail="可继续浏览页面"
    />
  );
}
