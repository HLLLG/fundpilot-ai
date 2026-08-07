import { describe, expect, it } from "vitest";
import type { Report } from "@/lib/api";
import {
  confidenceDisplayLabel,
  displayFundRecommendations,
  groupFundRecommendations,
  keyReasonLines,
  meaningfulNewsLines,
  portfolioRecommendationLines,
  safeDiagnosticMetrics,
  scopeReportToCurrentHoldings,
  selectNextTradingPlan,
  selectPrimaryReason,
} from "@/lib/reportPresentation";

type FundRec = Report["fund_recommendations"][number];

function rec(overrides: Partial<FundRec>): FundRec {
  return {
    fund_code: "000001",
    fund_name: "测试基金",
    action: "观察",
    points: ["保持观察"],
    ...overrides,
  };
}

describe("daily report presentation", () => {
  it("groups actionable recommendations and keeps pause ahead of watch", () => {
    const add = rec({ fund_code: "1", action: "分批加仓" });
    const watch = rec({ fund_code: "2", action: "观察" });
    const pause = rec({ fund_code: "3", action: "暂停追涨" });
    const reduce = rec({ fund_code: "4", action: "减仓评估" });
    expect(groupFundRecommendations([watch, add, pause, reduce])).toEqual({
      needsAction: [add, reduce],
      observing: [pause, watch],
    });
  });

  it("filters empty news placeholders", () => {
    expect(
      meaningfulNewsLines(["暂无明确利好", " 无 ", "真实政策利好", "真实政策利好"]),
    ).toEqual(["真实政策利好"]);
  });

  it("keeps portfolio lines while removing legacy per-fund strings", () => {
    const report = {
      fund_recommendations: [],
      recommendations: ["组合整体观望", "[000001 · 观察] 保持观察"],
    } as unknown as Report;
    expect(portfolioRecommendationLines(report)).toEqual(["组合整体观望"]);
  });

  it("parses legacy per-fund recommendation strings", () => {
    const report = {
      fund_recommendations: [],
      recommendations: ["[000001 · 观察] 保持观察", "[000001 · 观察] 等待企稳"],
    } as unknown as Report;
    expect(displayFundRecommendations(report)).toEqual([
      {
        fund_code: "000001",
        fund_name: "000001",
        action: "观察",
        points: ["保持观察", "等待企稳"],
      },
    ]);
  });

  it("selects position basis before non-guard points", () => {
    expect(
      selectPrimaryReason(
        rec({
          suggested_position_change_basis: "集中度超过上限",
          points: ["已按风控规则调整", "板块资金偏弱"],
        }),
      ),
    ).toBe("集中度超过上限");
  });

  it("extracts the next-trading-day conditional plan", () => {
    expect(
      selectNextTradingPlan(["资金偏弱", "下交易日：若再跌2%则减仓"]),
    ).toBe("下交易日：若再跌2%则减仓");
  });

  it("keeps only non-duplicated explanatory reasons", () => {
    expect(
      keyReasonLines(
        rec({
          points: ["已按风控规则调整", "资金偏弱", "下交易日：若再跌2%则减仓", "资金偏弱"],
        }),
      ),
    ).toEqual(["资金偏弱"]);
  });

  it("maps confidence into beginner-facing reference labels", () => {
    expect(confidenceDisplayLabel("高")).toBe("参考度：高");
    expect(confidenceDisplayLabel("中")).toBe("参考度：中");
    expect(confidenceDisplayLabel("低")).toBe("参考度：有限");
    expect(confidenceDisplayLabel(undefined)).toBeNull();
  });

  it("hides impossible diagnostic values but preserves normal hints", () => {
    expect(
      safeDiagnosticMetrics({ return_1y_percent: 8220.94, max_drawdown_1y_percent: -160.53 }),
    ).toEqual({ hints: [], invalid: true });
    expect(
      safeDiagnosticMetrics({ return_1y_percent: 12.3, max_drawdown_1y_percent: -18.6 }),
    ).toEqual({ hints: ["近1年 12.3%", "最大回撤 -18.6%"], invalid: false });
  });

  it("scopes the latest report view to the current authoritative holdings", () => {
    const report = {
      holdings: [
        { fund_code: "010236", fund_name: "当前基金", holding_amount: 1_000 },
        { fund_code: "021627", fund_name: "旧基金", holding_amount: 2_000 },
      ],
      snapshots: [
        { fund_code: "010236", fund_name: "当前基金" },
        { fund_code: "021627", fund_name: "旧基金" },
      ],
      fund_recommendations: [
        rec({ fund_code: "010236", fund_name: "当前基金" }),
        rec({ fund_code: "021627", fund_name: "旧基金" }),
      ],
      analysis_facts: {
        holdings: [{ fund_code: "010236" }, { fund_code: "021627" }],
      },
    } as unknown as Report;

    const scoped = scopeReportToCurrentHoldings(report, [
      {
        fund_code: "010236",
        fund_name: "当前基金",
        holding_amount: 1_000,
        return_percent: 0,
      },
    ]);

    expect(scoped.hiddenRecommendationCount).toBe(1);
    expect(scoped.report.fund_recommendations.map((item) => item.fund_code)).toEqual([
      "010236",
    ]);
    expect(scoped.report.holdings.map((item) => item.fund_code)).toEqual(["010236"]);
    expect(scoped.report.snapshots.map((item) => item.fund_code)).toEqual(["010236"]);
    expect(
      (scoped.report.analysis_facts as { holdings: Array<{ fund_code: string }> }).holdings,
    ).toEqual([{ fund_code: "010236" }]);
    expect(report.fund_recommendations).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// 回归：列表摘要占位不得让日报整页崩溃
//
// 线上报障 7bc1ab07dce7a312833f107ecb3004f9：
// `TypeError: Cannot read properties of undefined (reading 'length')`，
// 抛在 `scopeReportToCurrentHoldings` 读 `report.fund_recommendations.length` 时。
//
// 成因：后端 `_REPORT_SUMMARY_FIELDS`（apps/api/app/database.py）有意不在
// `GET /api/reports` 里下发正文数组，而 Dashboard 的 `hydrateReport` 会先把列表摘要
// 当占位 Report 渲染、再按 id 拉正文。`Report` 类型却把那些数组声明为必填，
// 于是 typecheck / lint / build 全都拦不住 —— 修复前这四道检查都是绿的。
// 只有真的喂一个残缺对象进来才能发现。
//
// 所以这几个用例的作用是钉住两件事：
//   1. `asArray()` 那些守卫不是"多余的类型判断"，删掉就会重新白屏；
//   2. 补空数组之后，正文到达时按持仓收窄的行为必须依旧生效。
// ---------------------------------------------------------------------------

/** 只包含 `_REPORT_SUMMARY_FIELDS` 投影出的字段，正文数组一律缺失。 */
function summaryOnlyReport(): Report {
  return {
    id: "r-1",
    created_at: "2026-08-07T05:58:06Z",
    title: "今日日报",
    summary: "摘要",
    provider: "deepseek",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 1.2,
      alerts: [],
    },
    caveats: [],
    market_context: [],
  } as unknown as Report;
}

// 持仓必须非空：为空会命中 `!currentHoldings?.length` 短路，绕开真正的崩溃点。
// 线上那次正是"账户有持仓"的用户才触发的。
const currentHoldings = [
  { fund_code: "010236", fund_name: "当前基金", holding_amount: 1_000, return_percent: 0 },
];

describe("summary-only report placeholder", () => {
  it("scopes without throwing when the body arrays are absent entirely", () => {
    const report = summaryOnlyReport();
    expect(() => scopeReportToCurrentHoldings(report, currentHoldings)).not.toThrow();
    expect(scopeReportToCurrentHoldings(report, currentHoldings)).toEqual({
      report,
      hiddenRecommendationCount: 0,
    });
  });

  it("renders the remaining ReportPanel body as empty rather than crashing", () => {
    const report = summaryOnlyReport();
    expect(displayFundRecommendations(report)).toEqual([]);
    expect(portfolioRecommendationLines(report)).toEqual([]);
  });

  it("keeps narrowing to current holdings once the detail body arrives", () => {
    const detail = {
      ...summaryOnlyReport(),
      holdings: [],
      snapshots: [],
      recommendations: [],
      fund_recommendations: [
        rec({ fund_code: "010236", fund_name: "当前基金" }),
        rec({ fund_code: "021627", fund_name: "旧基金" }),
      ],
    } as unknown as Report;

    const scoped = scopeReportToCurrentHoldings(detail, currentHoldings);

    expect(scoped.hiddenRecommendationCount).toBe(1);
    expect(scoped.report.fund_recommendations.map((item) => item.fund_code)).toEqual([
      "010236",
    ]);
  });
});
