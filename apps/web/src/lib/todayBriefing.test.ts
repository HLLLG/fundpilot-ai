import { describe, expect, it } from "vitest";
import type { Holding, Report } from "@/lib/api";
import {
  extractBriefingDecisions,
  extractBriefingSummary,
  findTodayReport,
  greetingForHour,
  pickTopHoldings,
  resolveSectorPulse,
  truncateText,
} from "@/lib/todayBriefing";

function makeReport(overrides: Partial<Report> = {}): Report {
  return {
    id: "r1",
    created_at: "2026-06-21T08:00:00.000Z",
    title: "今日投研日报",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 1.2,
      alerts: [],
    },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    fund_recommendations: [
      {
        fund_code: "110011",
        fund_name: "易方达蓝筹精选",
        action: "观察",
        points: [],
      },
    ],
    summary: "半导体板块连涨三天，建议先观察，不急于追涨。",
    recommendations: ["整体仓位适中，保持耐心。"],
    caveats: [],
    provider: "offline",
    ...overrides,
  };
}

function makeHolding(overrides: Partial<Holding> = {}): Holding {
  return {
    fund_code: "110011",
    fund_name: "易方达蓝筹精选",
    holding_amount: 10000,
    return_percent: 5,
    sector_name: "半导体",
    sector_return_percent: 2.5,
    daily_profit: 120,
    ...overrides,
  };
}

describe("todayBriefing", () => {
  it("finds today's report from list", () => {
    const today = new Date().toISOString().slice(0, 10);
    const reports = [
      makeReport({ id: "old", created_at: "2026-06-20T08:00:00.000Z" }),
      makeReport({ id: "today", created_at: `${today}T09:00:00.000Z` }),
    ];
    expect(findTodayReport(reports)?.id).toBe("today");
  });

  it("extracts briefing summary from report", () => {
    const summary = extractBriefingSummary(makeReport());
    expect(summary.hasTodayReport).toBe(true);
    expect(summary.headline).toContain("半导体");
    expect(summary.focusFund).toBe("易方达蓝筹精选");
    expect(summary.riskLabel).toBe("中风险");
    expect(summary.actionLabel).toBe("观察");
  });

  it("returns placeholder when no report", () => {
    const summary = extractBriefingSummary(null);
    expect(summary.hasTodayReport).toBe(false);
    expect(summary.headline).toContain("还没有");
  });

  it("truncates long text", () => {
    const long = "a".repeat(150);
    expect(truncateText(long, 120).length).toBeLessThanOrEqual(121);
    expect(truncateText(long, 120).endsWith("…")).toBe(true);
  });

  it("picks top holdings by daily profit", () => {
    const holdings = [
      makeHolding({ fund_code: "1", daily_profit: 10 }),
      makeHolding({ fund_code: "2", daily_profit: 300 }),
      makeHolding({ fund_code: "3", daily_profit: 50 }),
    ];
    const top = pickTopHoldings(holdings, 2);
    expect(top.map((h) => h.fund_code)).toEqual(["2", "3"]);
  });

  it("resolves sector pulse by largest absolute move", () => {
    const pulse = resolveSectorPulse([
      makeHolding({ sector_name: "白酒", sector_return_percent: 0.5 }),
      makeHolding({ sector_name: "半导体", sector_return_percent: -3.2 }),
    ]);
    expect(pulse?.sectorName).toBe("半导体");
    expect(pulse?.returnPercent).toBe(-3.2);
  });

  it("extracts briefing decisions from report", () => {
    const decisions = extractBriefingDecisions(makeReport());
    expect(decisions.fundDecisions).toHaveLength(1);
    expect(decisions.fundDecisions[0]?.fundName).toBe("易方达蓝筹精选");
    expect(decisions.portfolioNotes.length).toBeGreaterThan(0);
  });

  it("returns greeting by hour", () => {
    expect(greetingForHour(9)).toBe("早上好");
    expect(greetingForHour(14)).toBe("下午好");
    expect(greetingForHour(21)).toBe("晚上好");
  });
});
