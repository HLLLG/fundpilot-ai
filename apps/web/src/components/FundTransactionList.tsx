"use client";

import { useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import type { DeletePortfolioTransactionResult, FundTransaction } from "@/lib/api";
import { deletePortfolioTransaction } from "@/lib/api";
import { sortLedgerTransactions } from "@/lib/transactionList";
import { userFacingErrorMessage } from "@/lib/userFacingError";

const MONEY = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatAmount(value: number) {
  return MONEY.format(value);
}

function statusLabel(tx: FundTransaction): { text: string; className: string } {
  if (tx.status === "confirmed") {
    return {
      text: "已确认",
      className: "bg-slate-100 text-slate-600",
    };
  }
  if (tx.in_progress) {
    return {
      text: "交易进行中",
      className: "bg-[var(--warn-bg)] text-[var(--warn-icon)]",
    };
  }
  if (tx.status === "pending") {
    return {
      text: "待确认",
      className: "bg-[var(--warn-bg)] text-[var(--warn-icon)]",
    };
  }
  if (tx.status === "superseded") {
    return { text: "已更正", className: "bg-slate-100 text-slate-500" };
  }
  return { text: "已跳过", className: "bg-slate-100 text-slate-500" };
}

function directionLabel(tx: FundTransaction) {
  return tx.direction === "buy" ? "买入" : "卖出";
}

export function FundTransactionList({
  transactions,
  showFundName = false,
  emptyText = "还没有交易记录",
  onDeleteTransaction,
  onDeleted,
}: {
  transactions: FundTransaction[];
  showFundName?: boolean;
  emptyText?: string;
  onDeleteTransaction?: (transactionId: string) => Promise<DeletePortfolioTransactionResult>;
  onDeleted?: (result: DeletePortfolioTransactionResult) => void;
}) {
  const ordered = sortLedgerTransactions(transactions);
  const [pendingDelete, setPendingDelete] = useState<FundTransaction | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  if (ordered.length === 0) {
    return <p className="px-1 py-8 text-center text-sm text-slate-500">{emptyText}</p>;
  }

  async function handleConfirmDelete() {
    if (!pendingDelete || deleting) {
      return;
    }
    setDeleteError(null);
    setDeleting(true);
    try {
      const result = onDeleteTransaction
        ? await onDeleteTransaction(pendingDelete.id)
        : await deletePortfolioTransaction(pendingDelete.id);
      onDeleted?.(result);
      setPendingDelete(null);
    } catch (error) {
      setDeleteError(userFacingErrorMessage(error, "删除失败，请稍后重试。"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <ul className="divide-y divide-slate-100">
        {ordered.map((tx) => {
          const buy = tx.direction === "buy";
          const status = statusLabel(tx);
          return (
            <li key={tx.id} className="flex items-start justify-between gap-2 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={`text-xs font-black ${
                      buy ? "text-[var(--danger-icon)]" : "text-[var(--success-icon)]"
                    }`}
                  >
                    {directionLabel(tx)}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${status.className}`}>
                    {status.text}
                  </span>
                </div>
                {showFundName ? (
                  <div className="mt-1 truncate text-sm font-bold text-slate-900">{tx.fund_name}</div>
                ) : null}
                <div className="mt-0.5 text-[11px] tabular-nums text-slate-500">{tx.trade_time}</div>
              </div>
              <div className="flex shrink-0 items-start gap-0.5">
                <div className="text-right">
                  <div
                    className={`text-sm font-black tabular-nums ${
                      buy ? "text-[var(--danger-icon)]" : "text-[var(--success-icon)]"
                    }`}
                  >
                    {buy ? "+" : "−"}
                    {formatAmount(tx.amount_yuan)}
                  </div>
                  {tx.fund_code ? (
                    <div className="mt-0.5 text-[10px] tabular-nums text-slate-400">{tx.fund_code}</div>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setDeleteError(null);
                    setPendingDelete(tx);
                  }}
                  className="inline-flex h-11 w-11 items-center justify-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                  aria-label={`删除${directionLabel(tx)} ${formatAmount(tx.amount_yuan)} 元`}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {pendingDelete ? (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-[var(--brand-ink)]/48 p-4 backdrop-blur-[6px]"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deleting) {
              setPendingDelete(null);
              setDeleteError(null);
            }
          }}
          role="presentation"
        >
          <div
            className="modal-sheet w-full max-w-sm rounded-[var(--radius-card)] p-5"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-transaction-title"
          >
            <h3 id="delete-transaction-title" className="text-base font-bold text-[var(--brand-deep)]">
              删除这笔交易？
            </h3>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
              将删除「{pendingDelete.fund_name}」的{directionLabel(pendingDelete)}{" "}
              {formatAmount(pendingDelete.amount_yuan)} 元。走势图买卖点会撤掉，持仓金额不变。金额请用「同步持仓」更新。
            </p>
            {deleteError ? (
              <p
                role="alert"
                className="mt-3 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-xs font-semibold leading-5 text-[var(--danger-fg)]"
              >
                {deleteError}
              </p>
            ) : null}
            <div className="mt-4 flex gap-2">
              <button
                ref={cancelButtonRef}
                type="button"
                disabled={deleting}
                onClick={() => {
                  setPendingDelete(null);
                  setDeleteError(null);
                }}
                className="btn-secondary min-h-11 flex-1 !py-2.5 disabled:opacity-60"
              >
                取消
              </button>
              <button
                type="button"
                disabled={deleting}
                onClick={() => void handleConfirmDelete()}
                className="min-h-11 flex-1 rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {deleting ? "删除中…" : deleteError ? "重试删除" : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
