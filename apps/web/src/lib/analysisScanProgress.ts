import {
  resolveScanTrack,
  type DiscoveryScanProgress,
  type DiscoveryScanStep,
  type DiscoveryScanTrack,
} from "@/lib/discoveryScanProgress";

export type AnalysisScanProgress = DiscoveryScanProgress;

export const ANALYSIS_SCAN_STEPS: readonly DiscoveryScanStep[] = [
  { id: "fund_data", label: "数据", hint: "净值诊断", stages: ["queued", "fund_data"] },
  { id: "news_prefetch", label: "新闻", hint: "市场新闻", stages: ["news_prefetch"] },
  { id: "news_summarize", label: "摘要", hint: "要闻摘要", stages: ["news_summarize"] },
  { id: "context", label: "上下文", hint: "整理材料", stages: ["context"] },
  { id: "generating", label: "分析", hint: "AI 日报", stages: ["generating", "salvage"] },
  { id: "judging", label: "审校", hint: "报告把关", stages: ["judging"] },
  { id: "saving", label: "落盘", hint: "保存报告", stages: ["saving"] },
];

export function resolveAnalysisScanTrack(progress: AnalysisScanProgress): DiscoveryScanTrack {
  return resolveScanTrack(progress, ANALYSIS_SCAN_STEPS, {
    aliases: (stage) =>
      stage.startsWith("tool_round_") || stage === "fetch_market_news"
        ? ANALYSIS_SCAN_STEPS.findIndex((step) => step.id === "news_prefetch")
        : null,
    fallbackHeadline: "正在生成日报…",
  });
}
