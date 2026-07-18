"use client";

import { useEffect, useRef, useState } from "react";
import { Plus, Search, X } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import type { FundSearchItem, ParsedTransaction } from "@/lib/api";
import { searchFunds } from "@/lib/api";
import { useDialogA11y } from "@/lib/useDialogA11y";

type BatchTransactionConfirmModalProps = {
  transactions: ParsedTransaction[];
  isBusy?: boolean;
  errorMessage?: string | null;
  onChange: (transactions: ParsedTransaction[]) => void;
  onConfirm: () => void;
  onContinueUpload: () => void;
  onClose: () => void;
};

function parseAmountInput(value: string): number {
  const parsed = Number.parseFloat(value.replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseOptionalFeeInput(value: string): {
  valid: boolean;
  value: number | null;
} {
  const normalized = value.replace(/,/g, "").trim();
  if (!normalized) {
    return { valid: true, value: null };
  }
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) {
    return { valid: false, value: null };
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) && parsed >= 0
    ? { valid: true, value: parsed }
    : { valid: false, value: null };
}

function FundCodeSearchPanel({
  initialQuery,
  onSelect,
  onClose,
}: {
  initialQuery: string;
  onSelect: (item: FundSearchItem) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [items, setItems] = useState<FundSearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (query.trim().length < 2) {
        setItems([]);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const results = await searchFunds(query.trim());
        if (!cancelled) {
          setItems(results);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "搜索失败");
          setItems([]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    const timer = window.setTimeout(() => {
      void run();
    }, 280);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  return (
    <div
      className="absolute left-0 right-0 top-full z-20 mt-1 max-h-48 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="搜索基金"
          placeholder="输入基金名称或代码"
          className="min-h-11 min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-blue-400"
          autoFocus
        />
        <button
          type="button"
          onClick={onClose}
          aria-label="取消基金搜索"
          className="min-h-11 shrink-0 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
        >
          取消
        </button>
      </div>
      {loading ? <div className="px-3 py-3 text-xs text-slate-500">搜索中...</div> : null}
      {error ? (
        <div role="alert" className="px-3 py-3 text-xs text-rose-700">
          {error}
        </div>
      ) : null}
      {!loading && !error && items.length === 0 ? (
        <div className="px-3 py-3 text-xs text-slate-500">输入名称或代码搜索</div>
      ) : null}
      {items.map((item) => (
        <button
          key={item.fund_code}
          type="button"
          onClick={() => onSelect(item)}
          aria-label={`选择 ${item.fund_name}（${item.fund_code}）`}
          className="flex min-h-11 w-full flex-col items-start justify-center gap-0.5 border-b border-slate-50 px-3 py-2.5 text-left transition hover:bg-blue-50"
        >
          <span className="text-xs font-bold tabular-nums text-blue-700">{item.fund_code}</span>
          <span className="text-xs text-slate-700">{item.fund_name}</span>
        </button>
      ))}
    </div>
  );
}

export function BatchTransactionConfirmModal({
  transactions,
  isBusy = false,
  errorMessage = null,
  onChange,
  onConfirm,
  onContinueUpload,
  onClose,
}: BatchTransactionConfirmModalProps) {
  const [searchIndex, setSearchIndex] = useState<number | null>(null);
  const [feeInputs, setFeeInputs] = useState<string[]>(() =>
    transactions.map((transaction) =>
      transaction.fee_yuan == null ? "" : String(transaction.fee_yuan),
    ),
  );
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const searchTriggerRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const requestClose = () => {
    if (!isBusy) {
      onClose();
    }
  };
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose: requestClose,
    initialFocusRef: closeButtonRef,
    closeOnEscape: searchIndex === null,
  });

  const closeSearch = (index: number | null = searchIndex) => {
    setSearchIndex(null);
    if (index != null) {
      window.requestAnimationFrame(() => searchTriggerRefs.current[index]?.focus());
    }
  };

  useEffect(() => {
    setFeeInputs((current) =>
      transactions.map((transaction, index) => {
        const draft = current[index];
        const parsed = draft == null ? null : parseOptionalFeeInput(draft);
        if (parsed?.valid && parsed.value === transaction.fee_yuan) {
          return draft;
        }
        return transaction.fee_yuan == null ? "" : String(transaction.fee_yuan);
      }),
    );
  }, [transactions]);

  const removeAt = (index: number) => {
    setFeeInputs((current) => current.filter((_, itemIndex) => itemIndex !== index));
    onChange(transactions.filter((_, itemIndex) => itemIndex !== index));
  };

  const updateAt = (index: number, patch: Partial<ParsedTransaction>) => {
    onChange(
      transactions.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );
  };

  const validCount = transactions.filter((tx) => Boolean(tx.fund_code)).length;
  const hasInvalidFee = feeInputs.some(
    (value) => !parseOptionalFeeInput(value).valid,
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-4 sm:items-center"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          requestClose();
        }
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-[28px] bg-white shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="batch-confirm-modal-title"
        aria-busy={isBusy}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 id="batch-confirm-modal-title" className="text-lg font-black text-slate-950">新增交易记录-支付宝</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              加仓（红）/减仓（绿）；代码未匹配时点「选择基金」从东财选取；可改方向、金额、时间和实际手续费。
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={requestClose}
            disabled={isBusy}
            className="touch-target inline-flex items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        {errorMessage ? (
          <div className="px-4 pt-4">
            <InlineNotice tone="error" message={errorMessage} />
          </div>
        ) : null}

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {transactions.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-500">未解析到交易记录。</p>
          ) : null}
          {transactions.map((tx, index) => {
            const isBuy = tx.direction === "buy";
            const unresolved = !tx.fund_code;
            const feeInput = feeInputs[index]
              ?? (tx.fee_yuan == null ? "" : String(tx.fee_yuan));
            const feeInputValid = parseOptionalFeeInput(feeInput).valid;
            return (
              <div
                key={`${tx.fund_name}-${tx.trade_time}-${index}`}
                className="relative rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3"
              >
                <button
                  type="button"
                  onClick={() => removeAt(index)}
                  className="touch-target absolute right-1 top-1 inline-flex items-center justify-center rounded-full text-slate-500 transition hover:bg-white hover:text-rose-600"
                  aria-label="移除此条"
                >
                  <X size={15} />
                </button>

                <div className="mb-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      updateAt(index, { direction: isBuy ? "sell" : "buy" })
                    }
                    className={`inline-flex min-h-11 min-w-11 items-center justify-center rounded-md px-2 py-2 text-xs font-black transition ${
                      isBuy
                        ? "bg-rose-100 text-rose-600 hover:bg-rose-200"
                        : "bg-emerald-100 text-emerald-700 hover:bg-emerald-200"
                    }`}
                    title="点击切换加仓/减仓"
                  >
                    {isBuy ? "加仓" : "减仓"}
                  </button>
                  {tx.in_progress ? (
                    <span className="rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-700">
                      交易进行中
                    </span>
                  ) : null}
                  {tx.confirm_date ? (
                    <span className="text-[11px] text-slate-500">确认日 {tx.confirm_date}</span>
                  ) : null}
                </div>

                <div className="space-y-2 pr-8">
                  <input
                    value={tx.fund_name}
                    aria-label={`基金名称：第 ${index + 1} 条交易`}
                    onChange={(event) => updateAt(index, { fund_name: event.target.value })}
                    className="min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-sm font-black text-slate-950 outline-none focus:border-blue-400"
                  />

                  <div className="relative">
                    <div className="flex items-center gap-2">
                      <input
                        value={tx.fund_code ?? ""}
                        inputMode="numeric"
                        aria-label={`基金代码：${tx.fund_name || `第 ${index + 1} 条交易`}`}
                        onChange={(event) => {
                          const next = event.target.value.replace(/\D/g, "").slice(0, 6);
                          updateAt(index, { fund_code: next || null });
                        }}
                        placeholder="待匹配代码"
                        className={`min-h-11 w-28 rounded-lg border px-2 py-2 text-xs font-bold tabular-nums outline-none focus:border-blue-400 ${
                          unresolved
                            ? "border-amber-300 bg-amber-50 text-amber-800"
                            : "border-slate-200 bg-white text-slate-800"
                        }`}
                      />
                      <button
                        ref={(node) => {
                          searchTriggerRefs.current[index] = node;
                        }}
                        type="button"
                        onClick={() => setSearchIndex(index)}
                        className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-2 text-[11px] font-semibold text-slate-600 transition hover:border-blue-300 hover:text-blue-700"
                      >
                        <Search size={12} />
                        选择基金
                      </button>
                    </div>
                    {searchIndex === index ? (
                      <FundCodeSearchPanel
                        initialQuery={tx.fund_name}
                        onSelect={(item) => {
                          updateAt(index, {
                            fund_code: item.fund_code,
                            fund_name: item.fund_name,
                          });
                          closeSearch(index);
                        }}
                        onClose={() => closeSearch(index)}
                      />
                    ) : null}
                  </div>

                  <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                      <div className="text-[11px] font-semibold text-slate-500">金额（元）</div>
                      <input
                        value={String(tx.amount_yuan ?? 0)}
                        inputMode="decimal"
                        aria-label={`交易金额：${tx.fund_name || `第 ${index + 1} 条交易`}`}
                        onChange={(event) =>
                          updateAt(index, { amount_yuan: parseAmountInput(event.target.value) })
                        }
                        className="mt-0.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 font-black tabular-nums text-slate-950 outline-none focus:border-blue-400"
                      />
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold text-slate-500">实际手续费（元）</div>
                      <input
                        value={feeInput}
                        inputMode="decimal"
                        min="0"
                        aria-invalid={!feeInputValid}
                        aria-label={`实际手续费：${tx.fund_name || `第 ${index + 1} 条交易`}`}
                        onChange={(event) => {
                          const next = event.target.value;
                          setFeeInputs((current) => {
                            const updated = [...current];
                            updated[index] = next;
                            return updated;
                          });
                          const parsed = parseOptionalFeeInput(next);
                          if (parsed.valid) {
                            updateAt(index, { fee_yuan: parsed.value });
                          }
                        }}
                        placeholder="未知留空"
                        className="mt-0.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 tabular-nums text-slate-800 outline-none focus:border-blue-400"
                      />
                      {!feeInputValid ? (
                        <p role="alert" className="mt-1 text-[10px] text-rose-600">
                          手续费须为大于等于 0 的数字；未知请留空
                        </p>
                      ) : null}
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold text-slate-500">成交时间</div>
                      <input
                        value={tx.trade_time}
                        aria-label={`成交时间：${tx.fund_name || `第 ${index + 1} 条交易`}`}
                        onChange={(event) =>
                          updateAt(index, { trade_time: event.target.value, confirm_date: null })
                        }
                        className="mt-0.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs tabular-nums text-slate-800 outline-none focus:border-blue-400"
                      />
                    </div>
                  </div>
                </div>
              </div>
            );
          })}

          <button
            type="button"
            onClick={onContinueUpload}
            disabled={isBusy}
            className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-blue-200 bg-blue-50/60 py-3 text-sm font-bold text-blue-600 transition hover:bg-blue-50 disabled:opacity-50"
          >
            <Plus size={15} />
            继续上传
          </button>
        </div>

        <div className="border-t border-slate-100 px-4 py-4">
          {transactions.some((tx) => !tx.fund_code) ? (
            <p className="mb-2 text-center text-[11px] text-amber-600">
              有未匹配代码的交易，确认时将自动跳过。
            </p>
          ) : null}
          {hasInvalidFee ? (
            <p className="mb-2 text-center text-[11px] text-rose-600">
              请修正手续费后再确认。
            </p>
          ) : null}
          <button
            type="button"
            disabled={isBusy || validCount === 0 || hasInvalidFee}
            onClick={onConfirm}
            className="w-full rounded-2xl bg-blue-600 px-4 py-3 text-sm font-black text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isBusy ? "正在应用..." : `完成（${validCount}）`}
          </button>
        </div>
      </div>
    </div>
  );
}
