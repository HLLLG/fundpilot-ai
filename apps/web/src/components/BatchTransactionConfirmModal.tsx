"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Search, X } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import type { FundSearchItem, ParsedTransaction } from "@/lib/api";
import { getFundTransactions, searchFunds } from "@/lib/api";
import { pickUniqueFundMatch } from "@/lib/fundNameMatch";
import { useDialogA11y } from "@/lib/useDialogA11y";
import { userFacingErrorMessage } from "@/lib/userFacingError";
import {
  recordedTransactionKey,
  resolveConfirmDate,
  resolveFirstReturnDate,
} from "@/lib/tradeConfirmDates";

type HeldFund = {
  fund_code: string;
  fund_name: string;
};

type BatchTransactionConfirmModalProps = {
  transactions: ParsedTransaction[];
  heldFunds?: HeldFund[];
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

type ConfirmBadge = {
  label: string;
  className: string;
};

function confirmBadgeFor({
  tx,
  index,
  transactions,
  heldCodes,
  recordedKeys,
  recordedReady,
}: {
  tx: ParsedTransaction;
  index: number;
  transactions: ParsedTransaction[];
  heldCodes: Set<string>;
  recordedKeys: Set<string>;
  recordedReady: boolean;
}): ConfirmBadge | null {
  const key = recordedTransactionKey(tx);
  if (tx.fund_code && recordedReady && recordedKeys.has(key)) {
    return { label: "已录入 · 将跳过", className: "bg-slate-200/80 text-slate-600" };
  }
  const duplicateInBatch = transactions
    .slice(0, index)
    .some(
      (prev) =>
        Boolean(prev.fund_code) && recordedTransactionKey(prev) === key,
    );
  if (duplicateInBatch) {
    return { label: "本批重复 · 将跳过", className: "bg-slate-200/80 text-slate-600" };
  }
  if (tx.direction === "buy") {
    if (tx.fund_code && heldCodes.has(tx.fund_code)) {
      return {
        label: "已持有 · 加仓",
        className: "bg-[var(--danger-bg)] text-[var(--danger-icon)]",
      };
    }
    const earlierBuy = transactions
      .slice(0, index)
      .some(
        (prev) =>
          prev.direction === "buy" &&
          Boolean(prev.fund_code) &&
          prev.fund_code === tx.fund_code,
      );
    if (earlierBuy) {
      return {
        label: "本批加仓",
        className: "bg-[var(--danger-bg)] text-[var(--danger-icon)]",
      };
    }
    return {
      label: "新建仓",
      className: "bg-[var(--info-bg)] text-[var(--info-fg)]",
    };
  }
  if (tx.fund_code && heldCodes.has(tx.fund_code)) {
    return {
      label: "已持有 · 减仓",
      className: "bg-[var(--success-bg)] text-[var(--success-icon)]",
    };
  }
  return {
    label: "减仓",
    className: "bg-[var(--success-bg)] text-[var(--success-icon)]",
  };
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
          setError(userFacingErrorMessage(err, "搜索失败"));
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
        <div role="alert" className="px-3 py-3 text-xs text-[var(--danger-fg)]">
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
          className="flex min-h-11 w-full flex-col items-start justify-center gap-0.5 border-b border-slate-50 px-3 py-2.5 text-left transition hover:bg-[var(--info-bg)]"
        >
          <span className="text-xs font-bold tabular-nums text-[var(--info-fg)]">{item.fund_code}</span>
          <span className="text-xs text-slate-700">{item.fund_name}</span>
        </button>
      ))}
    </div>
  );
}

export function BatchTransactionConfirmModal({
  transactions,
  heldFunds = [],
  isBusy = false,
  errorMessage = null,
  onChange,
  onConfirm,
  onContinueUpload,
  onClose,
}: BatchTransactionConfirmModalProps) {
  const [searchIndex, setSearchIndex] = useState<number | null>(null);
  const [recordedKeys, setRecordedKeys] = useState<Set<string>>(() => new Set());
  const [recordedReady, setRecordedReady] = useState(false);
  const heldCodeSet = useMemo(
    () => new Set(heldFunds.map((fund) => fund.fund_code).filter((code) => code && code !== "000000")),
    [heldFunds],
  );
  const recordedLookupKey = transactions
    .map((tx) => tx.fund_code)
    .filter((code): code is string => Boolean(code))
    .sort()
    .join(",");

  useEffect(() => {
    if (!recordedLookupKey) {
      setRecordedKeys(new Set());
      setRecordedReady(true);
      return;
    }
    const codes = recordedLookupKey.split(",");
    let cancelled = false;
    setRecordedReady(false);
    void Promise.all(
      codes.map((code) =>
        getFundTransactions(code)
          .then((result) => result.transactions)
          .catch(() => []),
      ),
    ).then((lists) => {
      if (cancelled) {
        return;
      }
      const keys = new Set<string>();
      for (const list of lists) {
        for (const tx of list) {
          keys.add(recordedTransactionKey(tx));
        }
      }
      setRecordedKeys(keys);
      setRecordedReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, [recordedLookupKey]);

  const transactionsRef = useRef(transactions);
  const heldFundsRef = useRef(heldFunds);
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    transactionsRef.current = transactions;
    heldFundsRef.current = heldFunds;
    onChangeRef.current = onChange;
  }, [heldFunds, onChange, transactions]);

  useEffect(() => {
    let cancelled = false;
    const snapshot = transactionsRef.current;
    const held = heldFundsRef.current ?? [];
    const withHeldCodes = snapshot.map((tx) => {
      if (tx.fund_code) {
        return tx;
      }
      const match = pickUniqueFundMatch(tx.fund_name, held);
      return match ? { ...tx, fund_code: match.fund_code } : tx;
    });
    const stillUnmatched = withHeldCodes
      .map((tx, index) => ({ tx, index }))
      .filter(({ tx }) => !tx.fund_code);
    const publish = (next: ParsedTransaction[]) => {
      if (cancelled) {
        return;
      }
      if (next.some((tx, index) => tx.fund_code !== snapshot[index]?.fund_code)) {
        onChangeRef.current(next);
      }
    };
    if (stillUnmatched.length === 0) {
      publish(withHeldCodes);
      return () => {
        cancelled = true;
      };
    }
    void Promise.all(
      stillUnmatched.map(async ({ tx, index }) => {
        const items = await searchFunds(tx.fund_name).catch(() => []);
        return { index, item: pickUniqueFundMatch(tx.fund_name, items) };
      }),
    ).then((hits) => {
      publish(
        withHeldCodes.map((tx, index) => {
          if (tx.fund_code) {
            return tx;
          }
          const hit = hits.find((row) => row.index === index)?.item;
          return hit ? { ...tx, fund_code: hit.fund_code } : tx;
        }),
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

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
  const skipCount = transactions.filter((tx, index) => {
    if (!tx.fund_code) {
      return false;
    }
    const key = recordedTransactionKey(tx);
    if (recordedReady && recordedKeys.has(key)) {
      return true;
    }
    return transactions
      .slice(0, index)
      .some((prev) => Boolean(prev.fund_code) && recordedTransactionKey(prev) === key);
  }).length;
  const applyCount = Math.max(0, validCount - skipCount);
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
            <h2 id="batch-confirm-modal-title" className="text-lg font-black text-slate-950">确认识别结果</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              请核对基金、方向、金额和成交时间后再写入。交易日 15:00 前成交，下一交易日起计收益；15:00 后则再下一交易日。同一基金不同时间会累加到同一持仓，完全相同的一笔会跳过。
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
            const confirmDate = tx.confirm_date ?? resolveConfirmDate(tx.trade_time);
            const firstReturnDate = tx.first_return_date ?? resolveFirstReturnDate(tx.trade_time);
            const badge = confirmBadgeFor({
              tx,
              index,
              transactions,
              heldCodes: heldCodeSet,
              recordedKeys,
              recordedReady,
            });
            return (
              <div
                key={`${tx.fund_name}-${tx.trade_time}-${index}`}
                className="relative rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3"
              >
                <button
                  type="button"
                  onClick={() => removeAt(index)}
                  className="touch-target absolute right-1 top-1 inline-flex items-center justify-center rounded-full text-slate-500 transition hover:bg-white hover:text-[var(--danger-icon)]"
                  aria-label="移除此条"
                >
                  <X size={15} />
                </button>

                <div className="mb-2 flex flex-wrap items-center gap-2 pr-6">
                  <button
                    type="button"
                    onClick={() =>
                      updateAt(index, { direction: isBuy ? "sell" : "buy" })
                    }
                    className={`inline-flex min-h-11 min-w-11 items-center justify-center rounded-md px-2 py-2 text-xs font-black transition ${
                      isBuy
                        ? "bg-[var(--danger-bg)] text-[var(--danger-icon)] hover:bg-[color-mix(in_srgb,var(--danger-bg)_80%,var(--danger-icon)_20%)]"
                        : "bg-[var(--success-bg)] text-[var(--success-icon)] hover:bg-[var(--success-bg)]"
                    }`}
                    title="点击切换买入/卖出"
                  >
                    {isBuy ? "买入" : "卖出"}
                  </button>
                  {badge ? (
                    <span className={`rounded-md px-2 py-0.5 text-[11px] font-bold ${badge.className}`}>
                      {badge.label}
                    </span>
                  ) : null}
                  {tx.in_progress ? (
                    <span className="rounded-md bg-[var(--warn-bg)] px-2 py-0.5 text-[11px] font-bold text-[var(--warn-icon)]">
                      交易进行中
                    </span>
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
                            ? "border-[var(--warn-border)] bg-[var(--warn-bg)] text-[var(--warn-fg)]"
                            : "border-slate-200 bg-white text-slate-800"
                        }`}
                      />
                      <button
                        ref={(node) => {
                          searchTriggerRefs.current[index] = node;
                        }}
                        type="button"
                        onClick={() => setSearchIndex(index)}
                        className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-2 text-[11px] font-semibold text-slate-600 transition hover:border-blue-300 hover:text-[var(--info-fg)]"
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
                        <p role="alert" className="mt-1 text-[10px] text-[var(--danger-icon)]">
                          手续费须为大于等于 0 的数字；未知请留空
                        </p>
                      ) : null}
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold text-slate-500">成交时间</div>
                      <input
                        value={tx.trade_time}
                        aria-label={`成交时间：${tx.fund_name || `第 ${index + 1} 条交易`}`}
                        onChange={(event) => {
                          const tradeTime = event.target.value;
                          updateAt(index, {
                            trade_time: tradeTime,
                            confirm_date: resolveConfirmDate(tradeTime),
                            first_return_date: resolveFirstReturnDate(tradeTime),
                          });
                        }}
                        className="mt-0.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs tabular-nums text-slate-800 outline-none focus:border-blue-400"
                      />
                    </div>
                  </div>
                  <p className="text-[11px] leading-5 text-slate-500">
                    确认净值日 {confirmDate ?? "—"}
                    <span className="mx-1.5 text-slate-300">·</span>
                    开始计收益 {firstReturnDate ?? "—"}
                  </p>
                </div>
              </div>
            );
          })}

          <button
            type="button"
            onClick={onContinueUpload}
            disabled={isBusy}
            className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-[var(--info-border)] bg-[var(--info-bg)]/80 py-3 text-sm font-bold text-blue-600 transition hover:bg-[var(--info-bg)] disabled:opacity-50"
          >
            <Plus size={15} />
            继续上传
          </button>
        </div>

        <div className="border-t border-slate-100 px-4 py-4">
          {transactions.some((tx) => !tx.fund_code) ? (
            <p className="mb-2 text-center text-[11px] text-[var(--warn-icon)]">
              有未匹配代码的交易，确认时将自动跳过。
            </p>
          ) : null}
          {skipCount > 0 ? (
            <p className="mb-2 text-center text-[11px] text-slate-500">
              {applyCount > 0
                ? `另有 ${skipCount} 笔已录入或重复，写入时将跳过。`
                : "这些交易均已录入，再次确认不会重复建仓。"}
            </p>
          ) : null}
          {hasInvalidFee ? (
            <p className="mb-2 text-center text-[11px] text-[var(--danger-icon)]">
              请修正手续费后再确认。
            </p>
          ) : null}
          <button
            type="button"
            disabled={isBusy || validCount === 0 || hasInvalidFee}
            onClick={onConfirm}
            className="w-full rounded-2xl bg-blue-600 px-4 py-3 text-sm font-black text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isBusy
              ? "正在应用..."
              : applyCount > 0
                ? `确认写入（${applyCount}）`
                : validCount > 0
                  ? "完成（全部已录入）"
                  : "确认写入（0）"}
          </button>
        </div>
      </div>
    </div>
  );
}
