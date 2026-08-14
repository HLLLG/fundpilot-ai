import type { FundTransaction } from "@/lib/api";

function isOpenTransaction(tx: FundTransaction): boolean {
  return Boolean(tx.in_progress) || tx.status === "pending";
}

/** 进行中/待确认在前，已确认在后；组内按成交时间、创建时间降序。 */
export function sortLedgerTransactions(transactions: FundTransaction[]): FundTransaction[] {
  return [...transactions].sort((left, right) => {
    const leftOpen = isOpenTransaction(left) ? 0 : 1;
    const rightOpen = isOpenTransaction(right) ? 0 : 1;
    if (leftOpen !== rightOpen) {
      return leftOpen - rightOpen;
    }
    const byTime = right.trade_time.localeCompare(left.trade_time);
    if (byTime !== 0) {
      return byTime;
    }
    return right.created_at.localeCompare(left.created_at);
  });
}
