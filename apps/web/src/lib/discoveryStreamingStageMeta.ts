/** 与后端 DISCOVERY_JOB_STAGES 对齐的荐基流式阶段。 */

export const DISCOVERY_STAGE_ORDER = [
  "connected",
  "sector_heat",
  "dip_prescreen",
  "candidate_pool",
  "news",
  "generating",
  "guarding",
  "saving",
] as const;

const STAGE_SHORT_LABEL: Record<string, string> = {
  connected: "已连接",
  sector_heat: "板块热度",
  dip_prescreen: "大跌预筛",
  candidate_pool: "候选池",
  news: "市场要闻",
  generating: "AI 分析",
  guarding: "校验推荐",
  saving: "保存报告",
  salvage: "断流恢复",
  fetch_market_news: "拉取新闻",
};

export function discoveryStageShortLabel(stage: string): string {
  if (stage.startsWith("tool_round_")) {
    return "检索新闻";
  }
  return STAGE_SHORT_LABEL[stage] ?? stage;
}
