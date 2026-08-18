import { describe, expect, it } from "vitest";
import {
  countSameDayKeys,
  markAlreadyRecordedTransactions,
  recordedTransactionKey,
  resolveConfirmDate,
  resolveFirstReturnDate,
  sameDayTransactionKey,
} from "./tradeConfirmDates";

describe("tradeConfirmDates", () => {
  it("counts profit from the next trading day when bought before 15:00", () => {
    expect(resolveConfirmDate("2026-08-13 14:55:30")).toBe("2026-08-13");
    expect(resolveFirstReturnDate("2026-08-13 14:55:30")).toBe("2026-08-14");
  });

  it("waits one extra trading day when bought at or after 15:00", () => {
    expect(resolveConfirmDate("2026-08-13 15:00:00")).toBe("2026-08-14");
    expect(resolveFirstReturnDate("2026-08-13 15:00:00")).toBe("2026-08-17");
  });

  it("skips weekends for both confirm and first-return dates", () => {
    expect(resolveConfirmDate("2026-08-15 10:00:00")).toBe("2026-08-17");
    expect(resolveFirstReturnDate("2026-08-15 10:00:00")).toBe("2026-08-18");
  });

  it("builds the same identity the ledger uses for duplicate detection", () => {
    expect(
      recordedTransactionKey({
        direction: "buy",
        fund_code: "000960",
        amount_yuan: 2000,
        trade_time: "2026-08-13 14:55:30",
      }),
    ).toBe("000960|buy|2026-08-13 14:55:30|2000");
  });

  it("treats the same fund/day/amount as one trade even when seconds differ", () => {
    expect(
      sameDayTransactionKey({
        direction: "buy",
        fund_code: "021959",
        amount_yuan: 1000,
        trade_time: "2026-08-17 14:55:30",
      }),
    ).toBe(
      sameDayTransactionKey({
        direction: "buy",
        fund_code: "021959",
        amount_yuan: 1000,
        trade_time: "2026-08-17 14:59:52",
      }),
    );
    const recorded = countSameDayKeys([
      {
        direction: "buy",
        fund_code: "021959",
        amount_yuan: 1000,
        trade_time: "2026-08-17 14:55:30",
      },
    ]);
    expect(
      markAlreadyRecordedTransactions(
        [
          {
            direction: "buy",
            fund_code: "021959",
            amount_yuan: 1000,
            trade_time: "2026-08-17 14:59:52",
          },
          {
            direction: "buy",
            fund_code: "021959",
            amount_yuan: 500,
            trade_time: "2026-08-14 14:59:57",
          },
        ],
        recorded,
      ),
    ).toEqual([true, false]);
  });

  it("marks only as many same-day rows as already stored", () => {
    const recorded = countSameDayKeys([
      {
        direction: "buy",
        fund_code: "021959",
        amount_yuan: 500,
        trade_time: "2026-08-17 14:50:00",
      },
    ]);
    expect(
      markAlreadyRecordedTransactions(
        [
          {
            direction: "buy",
            fund_code: "021959",
            amount_yuan: 500,
            trade_time: "2026-08-17 14:50:00",
          },
          {
            direction: "buy",
            fund_code: "021959",
            amount_yuan: 500,
            trade_time: "2026-08-17 14:52:00",
          },
        ],
        recorded,
      ),
    ).toEqual([true, false]);
  });
});
