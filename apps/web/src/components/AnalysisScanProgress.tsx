"use client";

import { ScanProgressTrack } from "@/components/ScanProgressTrack";
import {
  resolveAnalysisScanTrack,
  type AnalysisScanProgress,
} from "@/lib/analysisScanProgress";

type AnalysisScanProgressProps = {
  progress: AnalysisScanProgress;
};

export function AnalysisScanProgress({ progress }: AnalysisScanProgressProps) {
  return (
    <ScanProgressTrack
      progress={progress}
      track={resolveAnalysisScanTrack(progress)}
      eyebrow="Daily Chart · 日报航线"
      ariaName="日报航线"
      testId="analysis-scan-progress"
      stepTestIdPrefix="analysis-scan-step"
      faultHint="出错节点已标红，可重试本轮生成。"
    />
  );
}
