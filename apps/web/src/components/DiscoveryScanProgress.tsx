"use client";

import { ScanProgressTrack } from "@/components/ScanProgressTrack";
import {
  resolveDiscoveryScanTrack,
  type DiscoveryScanProgress,
} from "@/lib/discoveryScanProgress";

type DiscoveryScanProgressProps = {
  progress: DiscoveryScanProgress;
};

export function DiscoveryScanProgress({ progress }: DiscoveryScanProgressProps) {
  return (
    <ScanProgressTrack
      progress={progress}
      track={resolveDiscoveryScanTrack(progress)}
      eyebrow="Scan Chart · 扫描航线"
      ariaName="扫描航线"
      testId="discovery-scan-progress"
      stepTestIdPrefix="discovery-scan-step"
      faultHint="出错节点已标红，可重试本轮扫描。"
    />
  );
}
