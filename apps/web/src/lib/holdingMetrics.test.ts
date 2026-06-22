import { describe, expect, it } from "vitest";
import type { Holding } from "@/lib/api";
import { findHoldingIndex } from "@/lib/holdingMetrics";

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
