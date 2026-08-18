import { describe, expect, it } from "vitest";
import type { FundNavPoint, FundTransaction } from "@/lib/api";
import {
  buildHoldingProfitSeries,
  inferFirstHoldDate,
  periodProfitChange,
  sliceHoldingProfitSeries,
} from "./holdingProfitTrend";

function nav(date: string, value: number): FundNavPoint {
  return { date, nav: value };
}

function tx(overrides: Partial<FundTransaction> = {}): FundTransaction {
  return {
    id: "tx-1",
    fund_code: "011373",
    fund_name: "招商前沿医疗保健股票A",
    direction: "buy",
    amount_yuan: 4500,
    trade_time: "2026-06-12 14:30:00",
    confirm_date: "2026-06-12",
    status: "confirmed",
    shares_delta: 5347,
    nav_on_confirm: 0.8416,
    dedup_key: "k1",
    created_at: "2026-06-12T06:30:00Z",
    ...overrides,
  };
}

describe("inferFirstHoldDate", () => {
  it("uses the earlier of purchase date and holding-day backfill", () => {
    expect(inferFirstHoldDate("2026-06-12", 1, "2026-08-18")).toBe("2026-06-12");
    expect(inferFirstHoldDate("2026-08-17", 30, "2026-08-18")).toBe("2026-07-19");
  });

  it("walks back holding days on the local calendar", () => {
    expect(inferFirstHoldDate(null, 1, "2026-08-18")).toBe("2026-08-17");
  });
});

describe("buildHoldingProfitSeries", () => {
  const history = [
    nav("2026-06-11", 0.83),
    nav("2026-06-12", 0.8416),
    nav("2026-06-13", 0.85),
    nav("2026-08-14", 0.841),
    nav("2026-08-17", 0.854),
  ];

  it("replays a single buy and marks daily market profit after confirm", () => {
    const series = buildHoldingProfitSeries(history, [tx()]);
    expect(series[0]).toMatchObject({
      date: "2026-06-12",
      shares: 5347,
      dailyProfit: null,
    });
    expect(series[1].date).toBe("2026-06-13");
    expect(series[1].dailyProfit).toBeCloseTo(5347 * (0.85 - 0.8416), 1);
    expect(series[1].cumulativeProfit).toBeCloseTo(5347 * 0.85 - 4500, 1);
  });

  it("applies average-cost sells and ignores pending in-progress rows", () => {
    const series = buildHoldingProfitSeries(
      [
        nav("2026-06-12", 1),
        nav("2026-06-13", 1.1),
        nav("2026-06-16", 1.2),
      ],
      [
        tx({
          id: "buy",
          amount_yuan: 1000,
          shares_delta: 1000,
          nav_on_confirm: 1,
          confirm_date: "2026-06-12",
        }),
        tx({
          id: "sell",
          direction: "sell",
          amount_yuan: 550,
          shares_delta: -500,
          nav_on_confirm: 1.1,
          confirm_date: "2026-06-13",
          created_at: "2026-06-13T08:00:00Z",
        }),
        tx({
          id: "pending",
          status: "pending",
          in_progress: true,
          amount_yuan: 200,
          shares_delta: 200,
          confirm_date: "2026-06-16",
        }),
      ],
    );

    expect(series.map((point) => point.shares)).toEqual([1000, 500, 500]);
    expect(series[1].costBasis).toBeCloseTo(500, 2);
    expect(series[2].marketValue).toBeCloseTo(600, 2);
    expect(series[2].cumulativeProfit).toBeCloseTo(100, 2);
  });

  it("seeds leftover OCR shares before the first imported trade", () => {
    const series = buildHoldingProfitSeries(
      [nav("2026-06-10", 1), nav("2026-06-12", 1.1), nav("2026-06-13", 1.2)],
      [
        tx({
          amount_yuan: 100,
          shares_delta: 100,
          nav_on_confirm: 1.1,
          confirm_date: "2026-06-12",
        }),
      ],
      {
        shares: 1100,
        unitCost: 1,
        firstHoldDate: "2026-06-10",
      },
    );

    expect(series[0]).toMatchObject({ date: "2026-06-10", shares: 1000, cumulativeProfit: 0 });
    expect(series[1].shares).toBeCloseTo(1100, 4);
    expect(series[2].marketValue).toBeCloseTo(1320, 2);
  });

  it("falls back to constant shares when the ledger is empty", () => {
    const series = buildHoldingProfitSeries(history, [], {
      shares: 1000,
      unitCost: 0.8,
      firstHoldDate: "2026-08-14",
    });
    expect(series.map((point) => point.date)).toEqual(["2026-08-14", "2026-08-17"]);
    expect(series[0].cumulativeProfit).toBeCloseTo(41, 2);
    expect(series[1].dailyProfit).toBeCloseTo(13, 2);
  });

  it("backfills leftover shares before an add-on trade instead of starting at confirm date", () => {
    const series = buildHoldingProfitSeries(
      [nav("2026-08-14", 0.841), nav("2026-08-17", 0.854)],
      [
        tx({
          amount_yuan: 1000,
          shares_delta: 1170,
          nav_on_confirm: 0.854,
          confirm_date: "2026-08-17",
          trade_time: "2026-08-17 14:59:35",
        }),
      ],
      {
        shares: 5346.98,
        unitCost: 0.8416,
        firstHoldDate: "2026-08-17",
        holdingDays: 1,
        currentProfit: 66.32,
      },
    );

    expect(series.map((point) => point.date)).toEqual(["2026-08-14", "2026-08-17"]);
    expect(series[0].shares).toBeGreaterThan(4000);
    expect(series[1].shares).toBeCloseTo(5346.98, 1);
    expect(series[1].cumulativeProfit).toBeCloseTo(66.32, 2);
  });

  it("pins the latest cumulative profit to the displayed holding profit", () => {
    const series = buildHoldingProfitSeries(history, [tx()], {
      currentProfit: 66.32,
    });
    expect(series.at(-1)?.cumulativeProfit).toBeCloseTo(66.32, 2);
  });
});

describe("sliceHoldingProfitSeries", () => {
  it("keeps the latest window and reports period profit change", () => {
    const points = buildHoldingProfitSeries(
      [nav("2026-06-12", 1), nav("2026-06-13", 1.1), nav("2026-06-16", 1.2)],
      [tx({ amount_yuan: 1000, shares_delta: 1000, nav_on_confirm: 1 })],
    );
    const window = sliceHoldingProfitSeries(points, 2);
    expect(window).toHaveLength(2);
    expect(periodProfitChange(window)).toBeCloseTo(
      window[1].cumulativeProfit - window[0].cumulativeProfit,
      2,
    );
  });
});
