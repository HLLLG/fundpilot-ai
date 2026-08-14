import { describe, expect, it } from "vitest";
import {
  recordedTransactionKey,
  resolveConfirmDate,
  resolveFirstReturnDate,
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
});
