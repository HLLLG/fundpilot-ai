import { describe, expect, it } from "vitest";
import {
  formatHoldingsColumnDateShort,
  formatTradeDateShort,
  holdingsColumnAsOfIso,
} from "./tradeDateLabel";

describe("formatTradeDateShort", () => {
  it("turns an ISO date into MM-DD", () => {
    expect(formatTradeDateShort("2026-08-14")).toBe("08-14");
  });
});

describe("holdingsColumnAsOfIso", () => {
  it("uses today when the market is open for trading", () => {
    expect(
      holdingsColumnAsOfIso({
        is_trading_day: true,
        calendar_date: "2026-08-14",
        effective_trade_date: "2026-08-14",
      }),
    ).toBe("2026-08-14");
  });

  it("still uses today on a trading day even if quotes still point at yesterday", () => {
    expect(
      holdingsColumnAsOfIso({
        is_trading_day: true,
        calendar_date: "2026-08-14",
        effective_trade_date: "2026-08-13",
      }),
    ).toBe("2026-08-14");
  });

  it("uses the previous trading day when the market is closed", () => {
    expect(
      holdingsColumnAsOfIso({
        is_trading_day: false,
        calendar_date: "2026-08-15",
        effective_trade_date: "2026-08-14",
      }),
    ).toBe("2026-08-14");
  });
});

describe("formatHoldingsColumnDateShort", () => {
  it("formats the closed-market settlement date", () => {
    expect(
      formatHoldingsColumnDateShort({
        is_trading_day: false,
        calendar_date: "2026-08-15",
        effective_trade_date: "2026-08-14",
      }),
    ).toBe("08-14");
  });
});
