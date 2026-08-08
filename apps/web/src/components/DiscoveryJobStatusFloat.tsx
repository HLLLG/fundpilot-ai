"use client";

import { useEffect, useState } from "react";
import { CheckCircle, Loader2, XCircle } from "lucide-react";
import type { FundDiscoveryReport } from "@/lib/api";
import { fetchDiscoveryJob } from "@/lib/api";
import { userFacingErrorMessage } from "@/lib/userFacingError";
import { JobProgressCard } from "@/components/JobProgressCard";

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
          setError(userFacingErrorMessage(err, "扫描失败，请重试。"));
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

  if (state === "completed") {
    return (
      <JobProgressCard
        tone="neutral"
        testId="discovery-job-float"
        icon={<CheckCircle size={18} className="text-[var(--success-icon)]" />}
        title="推荐报告已生成"
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
