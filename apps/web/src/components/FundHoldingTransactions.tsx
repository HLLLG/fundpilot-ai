"use client";

import { useEffect, useState } from "react";
import { FundTransactionList } from "@/components/FundTransactionList";
import {
  getFundTransactions,
  type DeletePortfolioTransactionResult,
  type FundTransaction,
} from "@/lib/api";
import { userFacingErrorMessage } from "@/lib/userFacingError";

function visibleFundTransactions(transactions: FundTransaction[], fundCode: string) {
  return transactions.filter(
    (tx) =>
      tx.fund_code === fundCode && tx.status !== "skipped" && tx.status !== "superseded",
  );
}

export function FundHoldingTransactions({
  fundCode,
  enabled = true,
  refreshKey = 0,
  onDeleteTransaction,
  onTransactionsChanged,
}: {
  fundCode: string;
  enabled?: boolean;
  refreshKey?: number;
  onDeleteTransaction?: (transactionId: string) => Promise<DeletePortfolioTransactionResult>;
  onTransactionsChanged?: () => void;
}) {
  const [transactions, setTransactions] = useState<FundTransaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !fundCode || fundCode === "000000") {
      setTransactions([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setTransactions(null);
    void getFundTransactions(fundCode)
      .then((result) => {
        if (!cancelled) {
          setTransactions(visibleFundTransactions(result.transactions, fundCode));
        }
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(userFacingErrorMessage(loadError, "交易记录加载失败。"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, fundCode, refreshKey]);

  return (
    <section className="rounded-xl border border-slate-100 bg-white px-3">
      <h3 className="pt-3 text-xs font-bold text-slate-500">交易记录</h3>
      {error ? (
        <p role="alert" className="py-6 text-center text-sm text-[var(--danger-fg)]">
          {error}
        </p>
      ) : transactions == null ? (
        <p className="py-6 text-center text-sm text-slate-500">正在加载交易记录…</p>
      ) : (
        <FundTransactionList
          transactions={transactions}
          emptyText="这只基金还没有导入交易记录"
          onDeleteTransaction={onDeleteTransaction}
          onDeleted={(result) => {
            setTransactions(visibleFundTransactions(result.transactions, fundCode));
            onTransactionsChanged?.();
          }}
        />
      )}
    </section>
  );
}
