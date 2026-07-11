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
});
