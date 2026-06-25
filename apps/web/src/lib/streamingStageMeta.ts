/** 与后端 JOB_STAGES 对齐的流式分析阶段顺序（阶段 3 UI）。 */

export const ANALYSIS_STAGE_ORDER = [
  "fund_data",
  "news_prefetch",
  "news_summarize",
  "generating",
  "judging",
  "saving",
] as const;

export type AnalysisStageId = (typeof ANALYSIS_STAGE_ORDER)[number];

export type StageCardStatus = "done" | "active" | "pending";

const STAGE_SHORT_LABEL: Record<string, string> = {
  fund_data: "净值与诊断",
  news_prefetch: "市场新闻",
  news_summarize: "要闻摘要",
  generating: "AI 分析",
  judging: "报告审校",
  saving: "保存报告",
  salvage: "断流恢复",
  fetch_market_news: "拉取新闻",
};

export function stageShortLabel(stage: string): string {
  return STAGE_SHORT_LABEL[stage] ?? stage;
}

export function stageCardStatus(
  stage: string,
  currentStage: string,
  completedStages: ReadonlySet<string>,
): StageCardStatus {
  const currentIndex = ANALYSIS_STAGE_ORDER.indexOf(currentStage as AnalysisStageId);
  const index = ANALYSIS_STAGE_ORDER.indexOf(stage as AnalysisStageId);
  if (index < 0) {
    return stage === currentStage ? "active" : "pending";
  }
  if (completedStages.has(stage) && stage !== currentStage) {
    return "done";
  }
  if (stage === currentStage) {
    return "active";
  }
  if (currentIndex >= 0 && index < currentIndex) {
    return "done";
  }
  return "pending";
}

export function formatThinkingNote(
  field: string,
  value: unknown,
): string | null {
  if (field === "title" && typeof value === "string") {
    return `已生成标题：${value.slice(0, 48)}${value.length > 48 ? "…" : ""}`;
  }
  if (field === "summary" && typeof value === "string") {
    return `已生成摘要（${value.length} 字）`;
  }
  if (field === "fund_recommendation" && value && typeof value === "object") {
    const rec = value as { fund_code?: string; fund_name?: string; action?: string };
    const name = rec.fund_name ?? rec.fund_code ?? "持仓";
    const action = rec.action ? ` → ${rec.action}` : "";
    return `已完成 ${name}${action}`;
  }
  if (field === "caveats" && Array.isArray(value)) {
    return `已生成 ${value.length} 条风险提示`;
  }
  return null;
}
