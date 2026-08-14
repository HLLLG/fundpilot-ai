import { describe, expect, it } from "vitest";
import type { FundTransaction } from "@/lib/api";
import { buildTradeMarkers } from "./tradeMarkers";

function tx(overrides: Partial<FundTransaction>): FundTransaction {
  return {
    id: "1",
    fund_code: "000960",
    fund_name: "招商医疗保健股票A",
    direction: "buy",
    amount_yuan: 2000,
    trade_time: "2026-08-13 14:55:30",
    confirm_date: "2026-08-13",
    status: "confirmed",
    shares_delta: 100,
    nav_on_confirm: 1,
    dedup_key: "k",
    created_at: "2026-08-13T07:00:00Z",
    ...overrides,
  };
}

describe("buildTradeMarkers", () => {
  it("keeps buys and sells on the same day as separate red/green markers", () => {
    const markers = buildTradeMarkers([
      tx({ id: "b", direction: "buy", amount_yuan: 2000 }),
      tx({
        id: "s",
        direction: "sell",
        amount_yuan: 500,
        trade_time: "2026-08-13 10:00:00",
      }),
    ]);
    expect(markers.map((marker) => marker.kind)).toEqual(["buy", "sell"]);
    expect(markers.every((marker) => marker.date === "2026-08-13")).toBe(true);
  });

  it("ignores skipped and superseded ledger rows", () => {
    expect(
      buildTradeMarkers([
        tx({ id: "skip", status: "skipped" }),
        tx({ id: "old", status: "superseded", confirm_date: "2026-08-10" }),
        tx({ id: "keep", amount_yuan: 1300 }),
      ]),
    ).toEqual([
      expect.objectContaining({ date: "2026-08-13", kind: "buy" }),
    ]);
  });

  it("groups two buys of the same fund on different days", () => {
    const markers = buildTradeMarkers([
      tx({ id: "a", confirm_date: "2026-08-13", trade_time: "2026-08-13 14:55:30" }),
      tx({
        id: "b",
        confirm_date: "2026-08-10",
        trade_time: "2026-08-10 14:47:18",
        amount_yuan: 1500,
      }),
    ]);
    expect(markers).toHaveLength(2);
    expect(markers[0]).toMatchObject({ date: "2026-08-10", kind: "buy" });
    expect(markers[1]).toMatchObject({ date: "2026-08-13", kind: "buy" });
  });
});
