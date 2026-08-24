/** 发现基金扫描在页面上展示的整条航线，与后端 DISCOVERY_JOB_STAGES 对齐。 */

export type DiscoveryScanProgressStatus = "running" | "failed" | "completed";

export type DiscoveryScanProgress = {
  stage: string;
  stageLabel: string;
  status: DiscoveryScanProgressStatus;
  error?: string | null;
};

export type DiscoveryScanNodeState = "done" | "current" | "pending" | "failed";

export type DiscoveryScanStep = {
  id: string;
  label: string;
  hint: string;
  stages: readonly string[];
};

export const DISCOVERY_SCAN_STEPS: readonly DiscoveryScanStep[] = [
  { id: "start", label: "启动", hint: "接入扫描", stages: ["queued", "connected"] },
  { id: "sector_heat", label: "热度", hint: "板块热度", stages: ["sector_heat"] },
  { id: "candidate_pool", label: "候选", hint: "筛选基金", stages: ["candidate_pool"] },
  { id: "news", label: "要闻", hint: "市场新闻", stages: ["news", "fetch_market_news"] },
  { id: "generating", label: "分析", hint: "AI 研判", stages: ["generating", "salvage"] },
  { id: "guarding", label: "校验", hint: "规则把关", stages: ["guarding"] },
  { id: "saving", label: "落盘", hint: "保存报告", stages: ["saving"] },
];

export type DiscoveryScanTrackNode = {
  id: string;
  label: string;
  hint: string;
  state: DiscoveryScanNodeState;
};

export type DiscoveryScanTrack = {
  nodes: DiscoveryScanTrackNode[];
  currentIndex: number;
  fillPercent: number;
  headline: string;
  reachedCount: number;
  total: number;
};

export function scanStepIndex(
  stage: string,
  steps: readonly DiscoveryScanStep[],
  aliases?: (stage: string) => number | null,
): number {
  if (stage === "completed") {
    return steps.length;
  }
  const aliased = aliases?.(stage);
  if (aliased != null) {
    return aliased;
  }
  const index = steps.findIndex((step) => step.stages.includes(stage));
  return index >= 0 ? index : 0;
}

export function resolveScanTrack(
  progress: DiscoveryScanProgress,
  steps: readonly DiscoveryScanStep[],
  options?: {
    aliases?: (stage: string) => number | null;
    fallbackHeadline?: string;
  },
): DiscoveryScanTrack {
  const total = steps.length;
  const completedAll = progress.status === "completed" || progress.stage === "completed";
  const rawIndex = scanStepIndex(progress.stage, steps, options?.aliases);
  const currentIndex = completedAll ? total - 1 : Math.min(Math.max(rawIndex, 0), total - 1);

  const nodes = steps.map((step, index) => {
    let state: DiscoveryScanNodeState;
    if (completedAll) {
      state = "done";
    } else if (progress.status === "failed" && index === currentIndex) {
      state = "failed";
    } else if (index < currentIndex) {
      state = "done";
    } else if (index === currentIndex) {
      state = "current";
    } else {
      state = "pending";
    }
    return { id: step.id, label: step.label, hint: step.hint, state };
  });

  const fillPercent = completedAll || total <= 1
    ? 100
    : (currentIndex / (total - 1)) * 100;

  const headline = progress.status === "failed"
    ? (progress.error?.trim() || "扫描失败，请重试")
    : progress.stageLabel.trim() || steps[currentIndex]?.hint || options?.fallbackHeadline || "正在扫描…";

  return {
    nodes,
    currentIndex,
    fillPercent,
    headline,
    reachedCount: completedAll ? total : currentIndex + 1,
    total,
  };
}

export function discoveryScanStepIndex(stage: string): number {
  return scanStepIndex(stage, DISCOVERY_SCAN_STEPS, (value) =>
    value.startsWith("tool_round_")
      ? DISCOVERY_SCAN_STEPS.findIndex((step) => step.id === "news")
      : null,
  );
}

export function resolveDiscoveryScanTrack(progress: DiscoveryScanProgress): DiscoveryScanTrack {
  return resolveScanTrack(progress, DISCOVERY_SCAN_STEPS, {
    aliases: (stage) =>
      stage.startsWith("tool_round_")
        ? DISCOVERY_SCAN_STEPS.findIndex((step) => step.id === "news")
        : null,
    fallbackHeadline: "正在扫描…",
  });
}
