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
  it("sorts by trade time descending across days", () => {
    const goldAug14 = tx("gold-14", "2026-08-14 14:59:57", "confirmed");
    const rareEarth = tx("re-14", "2026-08-14 14:57:23", "confirmed");
    const goldAug17 = tx("gold-17", "2026-08-17 14:59:52", "confirmed");
    const medical = tx("med-17", "2026-08-17 14:59:35", "confirmed");
    const inProgressOlder = tx("p-old", "2026-08-14 14:44:52", "pending", true);

    expect(
      sortLedgerTransactions([
        goldAug14,
        rareEarth,
        goldAug17,
        medical,
        inProgressOlder,
      ]).map((item) => item.id),
    ).toEqual(["gold-17", "med-17", "gold-14", "re-14", "p-old"]);
  });
});
