// 持仓因子体检的展示/解读 helper（把设计文档第 3、4 章的因子语义写成纯函数）。
// 现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / Factor IC、PIT 与量化证据」。

import type { FactorKey, FundFactorScore } from "@/lib/api";

export type FactorTone = "good" | "warn" | "danger" | "neutral";

const FACTOR_LABELS: Record<FactorKey, string> = {
  momentum: "动量",
  risk_adjusted: "风险调整收益",
  drawdown: "回撤控制",
  size: "规模",
};

export function factorLabel(key: FactorKey): string {
  return FACTOR_LABELS[key];
}

// 因子 IC 置信标签配色（模块4 竖切3）：高=good / 中=warn / 低=danger / 不足·未知=neutral
export function factorReliabilityTone(level: string | undefined | null): FactorTone {
  if (level === "高") return "good";
  if (level === "中") return "warn";
  if (level === "低") return "danger";
  return "neutral";
}

export function formatPercentile(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return `${Math.round(value)}`;
}

// 综合等级配色：A 好 / B 中性 / C 警示 / D 风险
export function gradeTone(grade: string | null | undefined): FactorTone {
  switch (grade) {
    case "A":
      return "good";
    case "B":
      return "neutral";
    case "C":
      return "warn";
    case "D":
      return "danger";
    default:
      return "neutral";
  }
}

export function percentileTone(value: number | null | undefined): FactorTone {
  if (value == null) {
    return "neutral";
  }
  if (value >= 70) {
    return "good";
  }
  if (value >= 40) {
    return "neutral";
  }
  if (value >= 20) {
    return "warn";
  }
  return "danger";
}

const FACTOR_STRONG: Record<FactorKey, string> = {
  momentum: "近期赚钱势头在可比池中排在前列",
  risk_adjusted: "每单位回撤换来的收益在可比池中靠前",
  drawdown: "抗跌能力在可比池中较强",
  size: "规模在可比池中偏大、相对稳健",
};

const FACTOR_WEAK: Record<FactorKey, string> = {
  momentum: "近期赚钱势头在可比池中靠后",
  risk_adjusted: "每单位回撤换来的收益在可比池中靠后",
  drawdown: "抗跌能力在可比池中偏弱",
  size: "规模在可比池中偏小，留意流动性/清盘",
};

export function factorPercentileHint(
  key: FactorKey,
  percentile: number | null | undefined,
): string {
  if (percentile == null) {
    return "该因子数据不足，暂无法比较。";
  }
  if (percentile >= 60) {
    return `${FACTOR_STRONG[key]}（超过 ${Math.round(percentile)}% 同类）。`;
  }
  if (percentile >= 40) {
    return `${factorLabel(key)}处于可比池中游（约 ${Math.round(percentile)}% 分位）。`;
  }
  return `${FACTOR_WEAK[key]}（仅超过 ${Math.round(percentile)}% 同类）。`;
}

export function compositeSummary(fund: FundFactorScore): string {
  const score = fund.composite_score;
  if (score == null) {
    return "该基金因子数据不足，暂无法给出综合评分。";
  }
  const grade = fund.composite_grade ?? "";
  return `综合评分 ${Math.round(score)}（${grade} 级）——在排行榜可比池中超过约 ${Math.round(score)}% 的基金。`;
}
