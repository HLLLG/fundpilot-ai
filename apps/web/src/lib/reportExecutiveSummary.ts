import type { Report } from "@/lib/api";

const actionLabel: Record<string, string> = {
  watch: "组合建议：以观察为主",
  pause_add: "组合建议：暂停加仓，先复核风险",
  staggered_add: "组合建议：可在风控线内分批加仓",
  risk_review: "组合建议：触发风控复核，今日不宜新增加仓",
};

const riskLabel: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

function pickFocusFund(report: Report) {
  const funds = report.fund_recommendations;
  if (!funds.length) {
    return null;
  }
  const total = report.holdings.reduce((sum, item) => sum + item.holding_amount, 0) || 1;
  const ranked = [...funds].sort((left, right) => {
    const leftWeight =
      report.holdings.find((item) => item.fund_code === left.fund_code)?.holding_amount ?? 0;
    const rightWeight =
      report.holdings.find((item) => item.fund_code === right.fund_code)?.holding_amount ?? 0;
    return rightWeight / total - leftWeight / total;
  });
  return ranked[0];
}

export function buildExecutiveSummary(report: Report): [string, string, string] {
  const portfolioLine =
    report.recommendations.find((line) => line.includes("组合") || line.includes("风险")) ??
    actionLabel[report.risk.suggested_action] ??
    report.recommendations[0] ??
    report.summary;

  const alertLine =
    report.risk.alerts[0]?.message ??
    `组合风险等级：${riskLabel[report.risk.level] ?? report.risk.level}（加权收益 ${report.risk.weighted_return_percent}%）`;

  const focus = pickFocusFund(report);
  const fundLine = focus
    ? `今日重点：${focus.fund_name}（${focus.fund_code}）→ ${focus.action}`
    : report.summary;

  return [portfolioLine, alertLine, fundLine];
}
