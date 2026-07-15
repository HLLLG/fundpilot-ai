import type { Holding, Report } from "@/lib/api";
import { actionTone } from "@/lib/actionStyles";
import { translateEvidenceText } from "@/lib/decisionText";

export type FundRecommendation = Report["fund_recommendations"][number];
type Snapshot = Report["snapshots"][number];

export type CurrentPortfolioReportView = {
  report: Report;
  hiddenRecommendationCount: number;
};

const EMPTY_NEWS = new Set(["", "无", "暂无", "暂无利好", "暂无利空", "暂无明确利好", "暂无明确利空"]);
const ACTION_TONES = new Set(["add", "reduce", "deep_reduce", "clear_all"]);
const GUARD_NOTE = /已按.*(?:风控|规则).*调整|对照本地规则/;
const NEXT_PLAN = /(?:下一交易日|下交易日|开盘)/;

function normalizedFundName(value?: string | null): string {
  return (value ?? "").replace(/\s+/g, "").trim();
}

/**
 * The latest report can outlive a portfolio edit. Keep the stored report intact
 * for audit/export, but scope its active on-screen view to today's holdings so
 * deleted profile rows cannot still look like current positions.
 */
export function scopeReportToCurrentHoldings(
  report: Report,
  currentHoldings?: Holding[],
): CurrentPortfolioReportView {
  if (!currentHoldings?.length || !report.fund_recommendations.length) {
    return { report, hiddenRecommendationCount: 0 };
  }

  const codes = new Set(
    currentHoldings
      .map((holding) => holding.fund_code?.trim())
      .filter((code): code is string => Boolean(code && code !== "000000")),
  );
  const names = new Set(
    currentHoldings
      .map((holding) => normalizedFundName(holding.fund_name))
      .filter(Boolean),
  );
  const isCurrent = (item: { fund_code?: string | null; fund_name?: string | null }) => {
    const code = item.fund_code?.trim();
    if (code && code !== "000000") return codes.has(code);
    return names.has(normalizedFundName(item.fund_name));
  };

  const fundRecommendations = report.fund_recommendations.filter(isCurrent);
  const hiddenRecommendationCount =
    report.fund_recommendations.length - fundRecommendations.length;
  if (hiddenRecommendationCount <= 0) {
    return { report, hiddenRecommendationCount: 0 };
  }

  const facts = report.analysis_facts as
    | (Report["analysis_facts"] & { holdings?: Array<{ fund_code?: string; fund_name?: string }> })
    | undefined;
  const analysisFacts = facts
    ? {
        ...facts,
        holdings: Array.isArray(facts.holdings)
          ? facts.holdings.filter(isCurrent)
          : facts.holdings,
      }
    : report.analysis_facts;

  return {
    report: {
      ...report,
      holdings: report.holdings.filter(isCurrent),
      snapshots: report.snapshots.filter(isCurrent),
      fund_recommendations: fundRecommendations,
      analysis_facts: analysisFacts,
    },
    hiddenRecommendationCount,
  };
}

export function meaningfulNewsLines(values?: string[]): string[] {
  const result: string[] = [];
  for (const raw of values ?? []) {
    const value = raw.trim().replace(/[。；;]+$/, "");
    if (EMPTY_NEWS.has(value) || result.includes(value)) continue;
    result.push(value);
  }
  return result;
}

export function displayFundRecommendations(report: Report): FundRecommendation[] {
  if (report.fund_recommendations.length > 0) return report.fund_recommendations;
  const byCode = new Map<string, FundRecommendation>();
  for (const line of report.recommendations) {
    const match = line.match(/^\[(\d{6})\s*[·｜|]\s*([^\]]+)\]\s*(.*)$/);
    if (!match) continue;
    const [, fundCode, action, rest] = match;
    const point = rest.trim();
    const existing = byCode.get(fundCode);
    if (!existing) {
      byCode.set(fundCode, {
        fund_code: fundCode,
        fund_name: fundCode,
        action: action.trim(),
        points: point ? [point] : [],
      });
    } else if (point && !existing.points.includes(point)) {
      existing.points.push(point);
    }
  }
  return [...byCode.values()];
}

export function portfolioRecommendationLines(report: Report): string[] {
  if (report.fund_recommendations.length > 0) return report.recommendations;
  return report.recommendations.filter((line) => !/^\[\d{6}\s*[·｜|]/.test(line.trim()));
}

export function groupFundRecommendations(items: FundRecommendation[]) {
  const needsAction: FundRecommendation[] = [];
  const pauses: FundRecommendation[] = [];
  const watches: FundRecommendation[] = [];
  for (const item of items) {
    const tone = actionTone(item.action);
    const hasPositionChange =
      item.suggested_position_change_percent != null &&
      item.suggested_position_change_percent !== 0;
    if (ACTION_TONES.has(tone) || hasPositionChange) needsAction.push(item);
    else if (tone === "pause") pauses.push(item);
    else watches.push(item);
  }
  return { needsAction, observing: [...pauses, ...watches] };
}

export function selectPrimaryReason(item: FundRecommendation): string {
  const candidate =
    item.suggested_position_change_basis?.trim() ||
    item.amount_note?.trim() ||
    item.points.find((point) => point.trim() && !GUARD_NOTE.test(point)) ||
    item.points[0] ||
    "暂无需要立即操作的新增信号";
  return translateEvidenceText(candidate);
}

export function selectNextTradingPlan(points: string[]): string | null {
  const match = points.find((point) => NEXT_PLAN.test(point));
  return match ? translateEvidenceText(match) : null;
}

export function keyReasonLines(item: FundRecommendation): string[] {
  const result: string[] = [];
  for (const point of item.points) {
    if (GUARD_NOTE.test(point) || NEXT_PLAN.test(point)) continue;
    const value = translateEvidenceText(point.trim());
    if (value && !result.includes(value)) result.push(value);
    if (result.length === 3) break;
  }
  return result;
}

export function confidenceDisplayLabel(value?: string): string | null {
  if (!value) return null;
  if (value.includes("高")) return "参考度：高";
  if (value.includes("中")) return "参考度：中";
  return "参考度：有限";
}

export function safeDiagnosticMetrics(
  snapshot: Pick<Snapshot, "return_1y_percent" | "max_drawdown_1y_percent">,
): { hints: string[]; invalid: boolean } {
  const hints: string[] = [];
  let invalid = false;
  const yearly = snapshot.return_1y_percent;
  if (yearly != null) {
    if (Number.isFinite(yearly) && yearly >= -100 && yearly <= 1000) hints.push(`近1年 ${yearly}%`);
    else invalid = true;
  }
  const drawdown = snapshot.max_drawdown_1y_percent;
  if (drawdown != null) {
    if (Number.isFinite(drawdown) && drawdown >= -100 && drawdown <= 0) hints.push(`最大回撤 ${drawdown}%`);
    else invalid = true;
  }
  return { hints, invalid };
}
