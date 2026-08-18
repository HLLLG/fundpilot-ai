import type { FundTransaction } from "@/lib/api";

/** 按成交时间倒序；同一秒再按写入时间倒序。 */
export function sortLedgerTransactions(transactions: FundTransaction[]): FundTransaction[] {
  return [...transactions].sort((left, right) => {
    const byTime = right.trade_time.localeCompare(left.trade_time);
    if (byTime !== 0) {
      return byTime;
    }
    return right.created_at.localeCompare(left.created_at);
  });
}
