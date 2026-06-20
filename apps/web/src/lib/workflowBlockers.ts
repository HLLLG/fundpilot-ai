import type { Holding, HoldingFieldWarning, InvestorProfile } from "@/lib/api";
import { displayableHoldings } from "@/lib/holdingMetrics";

export type WorkflowBlocker = {
  id: string;
  severity: "error" | "warn" | "info";
  message: string;
};

export function buildWorkflowBlockers(input: {
  holdings: Holding[];
  warnings: HoldingFieldWarning[];
  profile: InvestorProfile;
  hasReportToday: boolean;
}): WorkflowBlocker[] {
  const blockers: WorkflowBlocker[] = [];
  const { warnings, profile } = input;
  const holdings = displayableHoldings(input.holdings);

  if (!holdings.length) {
    blockers.push({
      id: "no-holdings",
      severity: "error",
      message: "请先上传支付宝持仓截图或录入持仓。",
    });
    return blockers;
  }

  const errorWarnings = warnings.filter((item) => item.severity === "error");
  const warnWarnings = warnings.filter((item) => item.severity === "warn");

  if (errorWarnings.length) {
    blockers.push({
      id: "ocr-errors",
      severity: "error",
      message: `OCR 有 ${errorWarnings.length} 处严重异常，建议修正后再生成报告。`,
    });
  } else if (warnWarnings.length) {
    blockers.push({
      id: "ocr-warns",
      severity: "warn",
      message: `有 ${warnWarnings.length} 处待核对（多为收益符号/合计），确认无误后可生成。`,
    });
  }

  const missingSector = holdings.filter((h) => !h.sector_name?.trim());
  if (missingSector.length) {
    blockers.push({
      id: "missing-sector",
      severity: "info",
      message: `${missingSector.length} 只未识别关联板块，新闻主题可能偏少。`,
    });
  }

  const actualTotal = holdings.reduce((sum, h) => sum + h.holding_amount, 0);
  const weightDenominator =
    profile.expected_investment_amount != null && profile.expected_investment_amount > 0
      ? profile.expected_investment_amount
      : actualTotal || 1;
  const overLimit = holdings.filter(
    (h) => h.holding_amount / weightDenominator * 100 > profile.concentration_limit_percent,
  );
  if (overLimit.length) {
    blockers.push({
      id: "concentration",
      severity: "warn",
      message: `${overLimit.length} 只超过集中度上限 ${profile.concentration_limit_percent}%，报告可能建议减仓评估。`,
    });
  }

  if (!input.hasReportToday) {
    blockers.push({
      id: "no-report",
      severity: "info",
      message: "今日尚未生成日报，确认账户汇总后点击「生成今日基金操作日报」。",
    });
  }

  return blockers;
}

export function hasBlockingErrors(blockers: WorkflowBlocker[]): boolean {
  return blockers.some((item) => item.severity === "error");
}
