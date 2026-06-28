import { describe, expect, it } from "vitest";
import type { Holding } from "@/lib/api";
import {
  applySectorDailyEstimate,
  computeDailyProfit,
  findHoldingIndex,
  mergeHoldingsAppend,
  mergeSectorIntradayClose,
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

describe("mergeHoldingsAppend", () => {
  it("appends new OCR rows and updates matching fund by code", () => {
    const previous = [
      holding("010236", "广发电子信息传媒产业精选股票C"),
      holding("018957", "中航机遇领航混合C"),
    ];
    previous[0].holding_amount = 1000;
    const incoming = [
      {
        ...holding("021277", "广发全球精选股票(QDII)C"),
        holding_amount: 300.02,
      },
      {
        ...holding("018957", "中航机遇领航混合C"),
        holding_amount: 10210.43,
        holding_profit: 210.43,
      },
    ];
    const merged = mergeHoldingsAppend(previous, incoming);
    expect(merged).toHaveLength(3);
    expect(merged.find((h) => h.fund_code === "021277")?.holding_amount).toBe(300.02);
    expect(merged.find((h) => h.fund_code === "018957")?.holding_amount).toBe(10210.43);
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

  it("prefers incoming quote fields when refresh returns new sector data", () => {
    const previous = [
      {
        ...holding("010236", "广发电子信息传媒产业精选股票C"),
        sector_return_percent: 4.5,
        daily_profit: 51.67,
      },
    ];
    const incoming = [
      {
        ...holding("010236", "广发电子信息传媒产业精选股票C"),
        sector_return_percent: 1.2,
        daily_profit: 18.4,
      },
    ];
    const merged = mergeHoldingsPreserveQuoteFields(previous, incoming);
    expect(merged[0].sector_return_percent).toBe(1.2);
    expect(merged[0].daily_profit).toBe(18.4);
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

describe("mergeSectorIntradayClose", () => {
  it("updates only the sector board return from intraday close", () => {
    const current: Holding = {
      fund_code: "008586",
      fund_name: "华夏人工智能ETF联接C",
      holding_amount: 8671.67,
      return_percent: 9.12,
      sector_name: "人工智能",
      sector_return_percent: 3.66,
      daily_return_percent: 3.66,
      daily_return_percent_source: "official_nav",
      daily_profit: 317.32,
    };

    const merged = mergeSectorIntradayClose(current, -4.62);

    expect(merged.sector_return_percent).toBe(-4.62);
    expect(merged.sector_return_percent_source).toBe("closing_estimate");
    expect(merged.daily_return_percent).toBe(3.66);
    expect(merged.daily_return_percent_source).toBe("official_nav");
    expect(merged.daily_profit).toBe(317.32);
  });
});
