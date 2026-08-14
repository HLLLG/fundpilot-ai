import { describe, expect, it } from "vitest";
import type { FundTransaction } from "@/lib/api";
import { sortLedgerTransactions } from "@/lib/transactionList";

function tx(
  id: string,
  trade_time: string,
  status: FundTransaction["status"],
  in_progress = false,
): FundTransaction {
  return {
    id,
    fund_code: "011036",
    fund_name: "嘉实中证稀土产业ETF联接C",
    direction: "buy",
    amount_yuan: 300,
    trade_time,
    confirm_date: trade_time.slice(0, 10),
    status,
    shares_delta: status === "confirmed" ? 10 : null,
    nav_on_confirm: status === "confirmed" ? 1 : null,
    in_progress,
    dedup_key: id,
    created_at: `${trade_time.replace(" ", "T")}+08:00`,
  };
}

describe("sortLedgerTransactions", () => {
  it("puts in-progress rows above confirmed, then sorts each group by time desc", () => {
    const confirmedNewer = tx("c-new", "2026-08-14 14:56:15", "confirmed");
    const inProgressOldest = tx("p-old", "2026-08-14 14:44:52", "pending", true);
    const confirmedOlder = tx("c-old", "2026-08-13 14:55:30", "confirmed");
    const inProgressNewest = tx("p-new", "2026-08-14 14:57:23", "pending", true);

    expect(
      sortLedgerTransactions([
        confirmedNewer,
        inProgressOldest,
        confirmedOlder,
        inProgressNewest,
      ]).map((item) => item.id),
    ).toEqual(["p-new", "p-old", "c-new", "c-old"]);
  });
});
