import { describe, expect, it } from "vitest";
import type { Holding } from "@/lib/api";
import {
  applySectorDailyEstimate,
  computeDailyProfit,
  findHoldingIndex,
  mergeHoldingsPreserveQuoteFields,
} from "@/lib/holdingMetrics";
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

describe("mergeHoldingsPreserveQuoteFields", () => {
  it("keeps sector and daily quote fields until refresh fills incoming", () => {
    const previous = [
      holding("010236", "广发电子信息传媒产业精选股票C"),
      {
        ...holding("021533", "天弘半导体设备指数C"),
        sector_return_percent: 3.0,
        sector_name: "半导体材料",
        daily_profit: 12.5,
        daily_return_percent: 0.8,
      },
    ];
    previous[0] = {
      ...previous[0],
      sector_return_percent: 4.5,
      daily_profit: 51.67,
      holding_profit: 56.25,
    };
    const incoming = [
      {
        ...holding("010236", "广发电子信息传媒产业精选股票C"),
        holding_amount: 1556.25,
        holding_profit: 56.25,
      },
      {
        ...holding("021533", "天弘半导体设备指数C"),
        holding_amount: 3000,
        holding_profit: 0,
        return_percent: 0,
      },
    ];
    const merged = mergeHoldingsPreserveQuoteFields(previous, incoming);
    expect(merged[0].sector_return_percent).toBe(4.5);
    expect(merged[0].daily_profit).toBe(51.67);
    expect(merged[1].holding_amount).toBe(3000);
    expect(merged[1].holding_profit).toBe(0);
    expect(merged[1].sector_return_percent).toBe(3.0);
  });
});

describe("profit accrual defer", () => {
  const deferred: Holding = {
    fund_code: "008281",
    fund_name: "天弘半导体设备指数C",
    holding_amount: 3000,
    return_percent: 0,
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
