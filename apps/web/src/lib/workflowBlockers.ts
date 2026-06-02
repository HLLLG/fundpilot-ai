import type { Holding, HoldingFieldWarning, InvestorProfile, PortfolioSummary } from "@/lib/api";

export type WorkflowBlocker = {
  id: string;
  severity: "error" | "warn" | "info";
  message: string;
};

export function buildWorkflowBlockers(input: {
  holdings: Holding[];
  warnings: HoldingFieldWarning[];
  profile: InvestorProfile;
  portfolioSummary: PortfolioSummary | null;
  hasReportToday: boolean;
}): WorkflowBlocker[] {
  const blockers: WorkflowBlocker[] = [];
  const { holdings, warnings, profile, portfolioSummary } = input;

  if (!holdings.length) {
    blockers.push({
      id: "no-holdings",
      severity: "error",
      message: "请先上传养基宝总览截图或录入持仓。",
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

  const placeholderCodes = holdings.filter((h) => h.fund_code === "000000");
  if (placeholderCodes.length) {
    blockers.push({
      id: "missing-codes",
      severity: "warn",
      message: `${placeholderCodes.length} 只基金缺少代码，请在「基金档案」补全详情截图。`,
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

  const total = holdings.reduce((sum, h) => sum + h.holding_amount, 0) || 1;
  const overLimit = holdings.filter(
    (h) => h.holding_amount / total * 100 > profile.concentration_limit_percent,
  );
  if (overLimit.length) {
    blockers.push({
      id: "concentration",
      severity: "warn",
      message: `${overLimit.length} 只超过集中度上限 ${profile.concentration_limit_percent}%，报告可能建议减仓评估。`,
    });
  }

  if (portfolioSummary?.profiles?.length === 0) {
    blockers.push({
      id: "no-profiles",
      severity: "info",
      message: "尚未建立基金档案，总览缺代码时无法自动补全名称。",
    });
  }

  if (!input.hasReportToday) {
    blockers.push({
      id: "no-report",
      severity: "info",
      message: "今日尚未生成日报，完成校对后点击「生成今日基金操作日报」。",
    });
  }

  return blockers;
}

export function hasBlockingErrors(blockers: WorkflowBlocker[]): boolean {
  return blockers.some((item) => item.severity === "error");
}
