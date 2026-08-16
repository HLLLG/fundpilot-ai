"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { FundTransactionList } from "@/components/FundTransactionList";
import { getPortfolioTransactions, type FundTransaction } from "@/lib/api";
import { useDialogA11y } from "@/lib/useDialogA11y";
import { userFacingErrorMessage } from "@/lib/userFacingError";

export function HoldingsTransactionLedgerModal({ onClose }: { onClose: () => void }) {
  const [transactions, setTransactions] = useState<FundTransaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose,
    initialFocusRef: closeButtonRef,
  });

  useEffect(() => {
    let cancelled = false;
    void getPortfolioTransactions()
      .then((result) => {
        if (!cancelled) {
          setTransactions(
            result.transactions.filter(
              (tx) => tx.status !== "skipped" && tx.status !== "superseded",
            ),
          );
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
  }, []);

  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center bg-[var(--brand-ink)]/48 p-0 backdrop-blur-[6px] sm:items-center sm:p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="holdings-tx-ledger-title"
        className="modal-sheet flex max-h-[min(92dvh,720px)] w-full max-w-lg flex-col overflow-hidden sm:rounded-[var(--radius-card)]"
      >
        <header className="flex items-center justify-between gap-3 border-b border-[var(--line)] px-4 py-3">
          <h2 id="holdings-tx-ledger-title" className="text-base font-black text-[var(--brand-deep)]">
            交易记录
          </h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="inline-flex h-11 w-11 items-center justify-center rounded-full text-[var(--muted)] transition hover:bg-[var(--surface-muted)]"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4">
          {error ? (
            <p role="alert" className="py-8 text-center text-sm text-[var(--danger-fg)]">
              {error}
            </p>
          ) : transactions == null ? (
            <p className="py-8 text-center text-sm text-slate-500">正在加载交易记录…</p>
          ) : (
            <FundTransactionList
              transactions={transactions}
              showFundName
              emptyText="还没有导入过交易。持仓页「导入交易」会把买卖流水记在这里。"
            />
          )}
        </div>
      </div>
    </div>
  );
}
