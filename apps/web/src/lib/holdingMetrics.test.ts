import { describe, expect, it } from "vitest";
import type { Holding } from "@/lib/api";
import { applySectorDailyEstimate, computeDailyProfit, findHoldingIndex } from "@/lib/holdingMetrics";
import { getDailyProfit } from "@/lib/holdingDisplay";

function holding(fund_code: string, fund_name: string): Holding {
  return {
    fund_code,
    fund_name,
    holding_amount: 1000,
    return_percent: 1,
  };
}

describe("findHoldingIndex", () => {
  it("finds by fund_code after array reorder", () => {
    const ordered = [
      holding("010236", "广发电子信息传媒产业精选股票C"),
      holding("018957", "中航机遇领航混合发起C"),
    ];
    const reordered = [ordered[1], ordered[0]];

    expect(
      findHoldingIndex(reordered, {
        fund_code: "018957",
        fund_name: "中航机遇领航混合发起C",
      }),
    ).toBe(0);
    expect(
      findHoldingIndex(reordered, {
        fund_code: "010236",
        fund_name: "广发电子信息传媒产业精选股票C",
      }),
    ).toBe(1);
  });

  it("falls back to normalized fund name when code is missing", () => {
    const holdings = [holding("000000", "中航机遇领航混合发起C")];
    expect(
      findHoldingIndex(holdings, {
        fund_code: "000000",
        fund_name: "中航机遇领航混合发起C",
      }),
    ).toBe(0);
  });
});

describe("profit accrual defer", () => {
  const deferred: Holding = {
    fund_code: "008281",
    fund_name: "天弘半导体设备指数C",
    holding_amount: 3000,
    settled_holding_amount: 3000,
    daily_profit: 79.23,
    daily_return_percent: 2.65,
    daily_return_percent_source: "official_nav",
    sector_return_percent: 3.3,
    profit_accrual_deferred: true,
  };

  it("zeros daily profit when deferred even if official nav was persisted", () => {
    expect(computeDailyProfit(deferred)).toBe(0);
    expect(getDailyProfit(deferred)).toBe(0);
    const estimated = applySectorDailyEstimate(deferred);
    expect(estimated.daily_profit).toBe(0);
    expect(estimated.daily_return_percent_source).toBe("pending_accrual");
  });
});
