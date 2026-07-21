// 组合风险指标的解读话术 helper（把设计文档第 3 章的"参考刻度"写成函数）。
// 现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / 金融评估与路径风险」。

export type MetricTone = "good" | "warn" | "danger" | "neutral";

export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return value.toFixed(digits);
}

export function formatSignedPercent(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

// 夏普比率参考刻度（文档 3.3）
export function sharpeHint(value: number | null | undefined): string {
  if (value == null) {
    return "波动为零或样本不足，暂无法评估性价比。";
  }
  if (value < 0) {
    return "夏普为负，承担的风险还不如存银行。";
  }
  if (value < 1) {
    return "夏普一般，收益对得起风险但不算划算。";
  }
  if (value < 2) {
    return "夏普良好，风险换来的回报不错。";
  }
  if (value < 3) {
    return "夏普优秀，性价比很高。";
  }
  return "夏普极好，散户很难长期做到。";
}

export function sharpeTone(value: number | null | undefined): MetricTone {
  if (value == null) {
    return "neutral";
  }
  if (value < 0) {
    return "danger";
  }
  if (value < 1) {
    return "warn";
  }
  return "good";
}

// 索提诺：只惩罚下行波动（文档 3.4）
export function sortinoHint(value: number | null | undefined): string {
  if (value == null) {
    return "下行波动为零或样本不足，暂无法评估。";
  }
  if (value < 1) {
    return "下行风险偏高，亏起来不算温和。";
  }
  if (value < 2) {
    return "下行控制良好，跌的时候比较克制。";
  }
  return "下行控制优秀，波动主要来自上涨方向。";
}

// 最大回撤（文档 3.2）：负数，越接近 0 越好
export function maxDrawdownHint(value: number | null | undefined): string {
  if (value == null) {
    return "样本不足，暂无法评估历史最痛点。";
  }
  const magnitude = Math.abs(value);
  if (magnitude < 10) {
    return "历史回撤较浅，账户波动可控。";
  }
  if (magnitude < 20) {
    return "历史最坏从高点缩水中等，需扛得住。";
  }
  return "历史回撤较深，最坏情况要做好心理准备。";
}

export function maxDrawdownTone(value: number | null | undefined): MetricTone {
  if (value == null) {
    return "neutral";
  }
  const magnitude = Math.abs(value);
  if (magnitude < 10) {
    return "good";
  }
  if (magnitude < 20) {
    return "warn";
  }
  return "danger";
}

// 年化波动率（文档 3.1）
export function volatilityHint(value: number | null | undefined): string {
  if (value == null) {
    return "样本不足，暂无法评估波动。";
  }
  if (value < 10) {
    return "波动较低，接近稳健债基的体验。";
  }
  if (value < 25) {
    return "中等波动，介于债基与单只股票之间。";
  }
  return "波动偏高，坐过山车的概率不小。";
}

export function volatilityTone(value: number | null | undefined): MetricTone {
  if (value == null) {
    return "neutral";
  }
  if (value < 10) {
    return "good";
  }
  if (value < 25) {
    return "warn";
  }
  return "danger";
}

// Beta（文档 3.5）：对沪深300 的敏感度
export function betaHint(value: number | null | undefined): string {
  if (value == null) {
    return "与大盘对齐数据不足，暂无法估算敏感度。";
  }
  if (value < 0) {
    return "Beta 为负，组合方向与大盘相反（罕见，多为对冲）。";
  }
  if (value < 0.8) {
    return "Beta 偏低，比大盘更防守，跌时也更抗摔。";
  }
  if (value <= 1.2) {
    return "Beta 接近 1，基本跟大盘同步起落。";
  }
  return "Beta 偏高，比大盘更激进，涨跌都被放大。";
}

export function betaTone(value: number | null | undefined): MetricTone {
  if (value == null) {
    return "neutral";
  }
  if (value > 1.5 || value < 0) {
    return "warn";
  }
  return "neutral";
}

// Alpha（文档 3.5）：剔除大盘后靠自己多赚的部分
export function alphaHint(value: number | null | undefined): string {
  if (value == null) {
    return "与大盘对齐数据不足，暂无法估算超额收益。";
  }
  if (value >= 0) {
    return "Alpha 为正，承担同等风险还跑赢了大盘，是真本事。";
  }
  return "Alpha 为负，剔除大盘影响后其实跑输了。";
}

export function alphaTone(value: number | null | undefined): MetricTone {
  if (value == null) {
    return "neutral";
  }
  return value >= 0 ? "good" : "danger";
}

// HHI / 有效持仓数（文档 3.7）
export function concentrationHint(
  hhi: number | null | undefined,
  effectiveHoldings: number | null | undefined,
): string {
  if (hhi == null || effectiveHoldings == null) {
    return "暂无持仓权重数据。";
  }
  if (hhi < 0.2) {
    return `分散度良好，实际相当于分散在约 ${effectiveHoldings.toFixed(1)} 只。`;
  }
  if (hhi < 0.4) {
    return `集中度中等，实际相当于约 ${effectiveHoldings.toFixed(1)} 只。`;
  }
  return `集中度偏高，实际相当于约 ${effectiveHoldings.toFixed(1)} 只，抗单一方向风险弱。`;
}

export function concentrationTone(hhi: number | null | undefined): MetricTone {
  if (hhi == null) {
    return "neutral";
  }
  if (hhi < 0.2) {
    return "good";
  }
  if (hhi < 0.4) {
    return "warn";
  }
  return "danger";
}
