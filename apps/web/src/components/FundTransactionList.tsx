"use client";

import type { FundTransaction } from "@/lib/api";

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

export function FundTransactionList({
  transactions,
  showFundName = false,
  emptyText = "还没有交易记录",
}: {
  transactions: FundTransaction[];
  showFundName?: boolean;
  emptyText?: string;
}) {
  if (transactions.length === 0) {
    return <p className="px-1 py-8 text-center text-sm text-slate-500">{emptyText}</p>;
  }

  return (
    <ul className="divide-y divide-slate-100">
      {transactions.map((tx) => {
        const buy = tx.direction === "buy";
        const status = statusLabel(tx);
        return (
          <li key={tx.id} className="flex items-start justify-between gap-3 py-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  className={`text-xs font-black ${
                    buy ? "text-[var(--danger-icon)]" : "text-[var(--success-icon)]"
                  }`}
                >
                  {buy ? "买入" : "卖出"}
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
            <div className="shrink-0 text-right">
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
          </li>
        );
      })}
    </ul>
  );
}
