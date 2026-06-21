import type { Holding, Report } from "@/lib/api";
import { displayableHoldings } from "@/lib/holdingMetrics";
import { getDailyProfit } from "@/lib/holdingDisplay";

const ACTION_LABEL: Record<Report["risk"]["suggested_action"], string> = {
  watch: "观察",
  pause_add: "暂停加仓",
  staggered_add: "分批加仓",
  risk_review: "减仓复核",
};

const RISK_LABEL: Record<Report["risk"]["level"], string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export type BriefingSummary = {
  hasTodayReport: boolean;
  headline: string;
  detail?: string;
  focusFund?: string;
  focusAction?: string;
  riskLevel?: Report["risk"]["level"];
  riskLabel?: string;
  actionLabel?: string;
  reportId?: string;
};

export type SectorPulse = {
  sectorName: string;
  returnPercent: number;
  fundName?: string;
};

export function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export function findTodayReport(reports: Report[]): Report | null {
  const today = todayIsoDate();
  const sorted = [...reports].sort(
    (left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
  );
  return sorted.find((report) => report.created_at.slice(0, 10) === today) ?? null;
}

export function truncateText(text: string, maxLength: number): string {
  const trimmed = text.trim();
  if (trimmed.length <= maxLength) {
    return trimmed;
  }
  return `${trimmed.slice(0, maxLength).trimEnd()}…`;
}

export function extractBriefingSummary(report: Report | null): BriefingSummary {
  if (!report) {
    return {
      hasTodayReport: false,
      headline: "今天还没有你的专属简报",
      detail: "生成一份投研日报，AI 会用说人话的方式告诉你：该不该动、动哪只、怎么动。",
    };
  }

  const topRec = report.fund_recommendations[0];
  const headline = truncateText(report.summary || report.title, 120);

  return {
    hasTodayReport: true,
    headline,
    detail: report.recommendations[0] ? truncateText(report.recommendations[0], 80) : undefined,
    focusFund: topRec ? `${topRec.fund_name}` : undefined,
    focusAction: topRec?.action,
    riskLevel: report.risk.level,
    riskLabel: RISK_LABEL[report.risk.level],
    actionLabel: ACTION_LABEL[report.risk.suggested_action],
    reportId: report.id,
  };
}

export function pickTopHoldings(holdings: Holding[], limit = 3): Holding[] {
  const display = displayableHoldings(holdings);
  return [...display]
    .sort((left, right) => {
      const leftDaily = getDailyProfit(left) ?? Number.NEGATIVE_INFINITY;
      const rightDaily = getDailyProfit(right) ?? Number.NEGATIVE_INFINITY;
      return rightDaily - leftDaily;
    })
    .slice(0, limit);
}

export function resolveSectorPulse(holdings: Holding[]): SectorPulse | null {
  const display = displayableHoldings(holdings);
  let best: SectorPulse | null = null;
  let bestAbs = -1;

  for (const holding of display) {
    const sectorName = holding.sector_name?.trim();
    const returnPercent = holding.sector_return_percent;
    if (!sectorName || returnPercent == null) {
      continue;
    }
    const abs = Math.abs(returnPercent);
    if (abs > bestAbs) {
      bestAbs = abs;
      best = {
        sectorName,
        returnPercent,
        fundName: holding.fund_name,
      };
    }
  }

  return best;
}

export type BriefingFundDecision = {
  fundCode: string;
  fundName: string;
  action: string;
  point?: string;
};

export type BriefingDecisions = {
  portfolioNotes: string[];
  fundDecisions: BriefingFundDecision[];
};

function parseFundRecommendations(report: Report): BriefingFundDecision[] {
  if (report.fund_recommendations.length > 0) {
    return report.fund_recommendations.map((item) => ({
      fundCode: item.fund_code,
      fundName: item.fund_name,
      action: item.action,
      point: item.points[0] ? truncateText(item.points[0], 72) : undefined,
    }));
  }

  const byCode = new Map<string, BriefingFundDecision>();
  for (const line of report.recommendations) {
    const match = line.match(/^\[(\d{6})\s*[·｜|]\s*([^\]]+)\]\s*(.*)$/);
    if (!match) {
      continue;
    }
    const [, fundCode, action, rest] = match;
    const existing = byCode.get(fundCode);
    if (!existing) {
      byCode.set(fundCode, {
        fundCode,
        fundName: fundCode,
        action: action.trim(),
        point: rest.trim() ? truncateText(rest.trim(), 72) : undefined,
      });
      continue;
    }
    if (rest.trim()) {
      existing.point = truncateText(rest.trim(), 72);
    }
  }
  return [...byCode.values()];
}

export function extractBriefingDecisions(report: Report | null, limit = 3): BriefingDecisions {
  if (!report) {
    return { portfolioNotes: [], fundDecisions: [] };
  }

  const fundDecisions = parseFundRecommendations(report).slice(0, limit);
  const portfolioNotes =
    report.fund_recommendations.length > 0
      ? report.recommendations.slice(0, 2).map((line) => truncateText(line, 96))
      : report.recommendations
          .filter((line) => !/^\[\d{6}\s*[·｜|]/.test(line.trim()))
          .slice(0, 2)
          .map((line) => truncateText(line, 96));

  return { portfolioNotes, fundDecisions };
}

export function greetingForHour(hour = new Date().getHours()): string {
  if (hour < 6) {
    return "夜深了";
  }
  if (hour < 12) {
    return "早上好";
  }
  if (hour < 18) {
    return "下午好";
  }
  return "晚上好";
}
