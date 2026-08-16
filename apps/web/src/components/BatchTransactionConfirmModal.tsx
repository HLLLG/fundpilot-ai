"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronUp, Plus, Search, X } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import { FundCodeSearchButton, ReviewEditRow } from "@/components/ocrReviewFields";
import { CLIPBOARD_IMAGE_PASTE_QUERY } from "@/components/ScreenshotIntakeExtras";
import type { FundSearchItem, ParsedTransaction } from "@/lib/api";
import { getFundTransactions, searchFunds } from "@/lib/api";
import { pickBestFundMatch, pickUniqueFundMatch } from "@/lib/fundNameMatch";
import { formatPlainMoney } from "@/lib/holdingMetrics";
import { collectImageFiles, ocrProgressLabel, type TransactionSyncPlan } from "@/lib/ocrBatchUpload";
import { useDialogA11y } from "@/lib/useDialogA11y";
import { useMediaQuery } from "@/lib/useMediaQuery";
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
  isUploading?: boolean;
  uploadProgress?: { current: number; total: number } | null;
  errorMessage?: string | null;
  onChange: (transactions: ParsedTransaction[]) => void;
  onConfirm: (plan: TransactionSyncPlan) => void;
  onContinueUpload?: () => void;
  onUploadMore?: (files: File[]) => void;
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
    return { label: "已录入", className: "text-slate-400" };
  }
  const duplicateInBatch = transactions
    .slice(0, index)
    .some(
      (prev) =>
        Boolean(prev.fund_code) && recordedTransactionKey(prev) === key,
    );
  if (duplicateInBatch) {
    return { label: "重复", className: "text-slate-400" };
  }
  if (tx.direction === "buy") {
    if (tx.fund_code && heldCodes.has(tx.fund_code)) {
      return { label: "加仓", className: "text-[var(--danger-icon)]" };
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
      return { label: "加仓", className: "text-[var(--danger-icon)]" };
    }
    return { label: "买入", className: "text-[var(--danger-icon)]" };
  }
  return { label: "减仓", className: "text-[var(--success-icon)]" };
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
      className="absolute left-0 right-0 top-11 z-20 mt-1 max-h-48 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg"
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
  isUploading = false,
  uploadProgress = null,
  errorMessage = null,
  onChange,
  onConfirm,
  onContinueUpload,
  onUploadMore,
  onClose,
}: BatchTransactionConfirmModalProps) {
  const [searchIndex, setSearchIndex] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const [syncPlan, setSyncPlan] = useState<TransactionSyncPlan>("apply_position");
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
      if (
        next.some(
          (tx, index) =>
            tx.fund_code !== snapshot[index]?.fund_code ||
            tx.match_source !== snapshot[index]?.match_source,
        )
      ) {
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
        const unique = pickUniqueFundMatch(tx.fund_name, items);
        const match = unique ?? pickBestFundMatch(tx.fund_name, items);
        return { index, item: match, similar: Boolean(match) && !unique };
      }),
    ).then((hits) => {
      publish(
        withHeldCodes.map((tx, index) => {
          if (tx.fund_code) {
            return tx;
          }
          const hit = hits.find((row) => row.index === index);
          if (!hit?.item) {
            return tx;
          }
          return {
            ...tx,
            fund_code: hit.item.fund_code,
            ...(hit.similar ? { match_source: "similar" } : {}),
          };
        }),
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const continueFileRef = useRef<HTMLInputElement>(null);
  const searchTriggerRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const isDesktopCompose = useMediaQuery(CLIPBOARD_IMAGE_PASTE_QUERY);
  const locked = isBusy || isUploading;
  const requestClose = () => {
    if (!locked) {
      onClose();
    }
  };
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose: requestClose,
    initialFocusRef: closeButtonRef,
    closeOnEscape: searchIndex === null && !planOpen,
  });

  const handleContinueUpload = () => {
    if (isDesktopCompose) {
      onContinueUpload?.();
      return;
    }
    if (onUploadMore) {
      continueFileRef.current?.click();
      return;
    }
    onContinueUpload?.();
  };

  const closeSearch = (index: number | null = searchIndex) => {
    setSearchIndex(null);
    if (index != null) {
      window.requestAnimationFrame(() => searchTriggerRefs.current[index]?.focus());
    }
  };

  const openSearch = (index: number) => {
    setSearchIndex(index);
    setSearchQuery(transactions[index]?.fund_name || transactions[index]?.fund_code || "");
  };

  const removeAt = (index: number) => {
    setSearchIndex((current) => (current === index ? null : current));
    setEditingIndex((current) => {
      if (current == null) {
        return current;
      }
      if (current === index) {
        return null;
      }
      return current > index ? current - 1 : current;
    });
    onChange(transactions.filter((_, itemIndex) => itemIndex !== index));
  };

  const updateAt = (index: number, patch: Partial<ParsedTransaction>) => {
    onChange(
      transactions.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );
  };

  const applySearchedFund = (index: number, item: FundSearchItem) => {
    updateAt(index, {
      fund_code: item.fund_code,
      fund_name: item.fund_name,
      match_source: null,
    });
    closeSearch(index);
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

  return (
    <div
      className="modal-backdrop fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center"
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
        className="modal-sheet flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-[var(--radius-card)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="batch-confirm-modal-title"
        aria-busy={locked}
      >
        <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-4">
          <h2 id="batch-confirm-modal-title" className="text-lg font-black text-[var(--brand-deep)]">
            确认识别结果
          </h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={requestClose}
            disabled={locked}
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

        <div className="min-h-0 flex-1 overflow-y-auto bg-[var(--surface-muted)] px-4 py-3">
          {transactions.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-500">未解析到交易记录。</p>
          ) : null}
          <div className="space-y-2">
          {transactions.map((tx, index) => {
            const isBuy = tx.direction === "buy";
            const unresolved = !tx.fund_code;
            const editing = editingIndex === index;
            const searching = searchIndex === index;
            const rowLabel = tx.fund_name || `第 ${index + 1} 条交易`;
            const confirmHint = unresolved || tx.match_source === "similar";
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
                className={`relative rounded-2xl bg-white px-4 pb-3 pt-3 shadow-[0_2px_12px_rgba(15,23,42,0.06)] ${
                  unresolved ? "ring-1 ring-[var(--warn-border)]" : ""
                }`}
              >
                <button
                  type="button"
                  onClick={() => removeAt(index)}
                  className="touch-target absolute right-1.5 top-1.5 z-10 inline-flex items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-[var(--danger-icon)]"
                  aria-label="移除此条"
                >
                  <X size={16} />
                </button>

                {editing ? (
                  <div className="relative pr-6">
                    <div className="mb-1 flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => updateAt(index, { direction: isBuy ? "sell" : "buy" })}
                        className={`text-[13px] font-medium ${badge?.className ?? "text-slate-500"}`}
                        title="点击切换买入/卖出"
                      >
                        {badge?.label ?? (isBuy ? "买入" : "卖出")}
                      </button>
                      {tx.in_progress ? (
                        <span className="text-[12px] font-medium text-[var(--warn-icon)]">交易进行中</span>
                      ) : null}
                    </div>
                    <FundCodeSearchButton
                      code={tx.fund_code ?? ""}
                      fundName={tx.fund_name}
                      unresolved={unresolved}
                      buttonRef={(node) => {
                        searchTriggerRefs.current[index] = node;
                      }}
                      onClick={() => openSearch(index)}
                    />
                    {confirmHint ? (
                      <p className="mb-2 text-[11px] leading-4 text-slate-500">请确认基金</p>
                    ) : null}
                    <ReviewEditRow
                      label="基金名称"
                      value={tx.fund_name}
                      ariaLabel={`基金名称：${rowLabel}`}
                      onChange={(value) => updateAt(index, { fund_name: value })}
                    />
                    <ReviewEditRow
                      label="金额"
                      value={String(tx.amount_yuan ?? 0)}
                      ariaLabel={`交易金额：${rowLabel}`}
                      inputMode="decimal"
                      onChange={(value) =>
                        updateAt(index, { amount_yuan: parseAmountInput(value) })
                      }
                    />
                    <ReviewEditRow
                      label="成交时间"
                      value={tx.trade_time}
                      ariaLabel={`成交时间：${rowLabel}`}
                      onChange={(value) =>
                        updateAt(index, {
                          trade_time: value,
                          confirm_date: resolveConfirmDate(value),
                          first_return_date: resolveFirstReturnDate(value),
                        })
                      }
                    />
                    <button
                      type="button"
                      onClick={() => setEditingIndex(null)}
                      className="mt-1 flex min-h-11 w-full flex-col items-center justify-center gap-0.5 rounded-xl text-xs font-medium text-[var(--brand)] hover:bg-[var(--brand-soft)]"
                    >
                      <ChevronUp size={16} strokeWidth={2.25} />
                      收起
                    </button>
                    {searching ? (
                      <FundCodeSearchPanel
                        initialQuery={searchQuery}
                        onSelect={(item) => applySearchedFund(index, item)}
                        onClose={() => closeSearch(index)}
                      />
                    ) : null}
                  </div>
                ) : (
                  <div className="relative pr-6">
                    <div className="mb-1.5 flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => updateAt(index, { direction: isBuy ? "sell" : "buy" })}
                        className={`text-[13px] font-medium ${badge?.className ?? "text-slate-500"}`}
                        title="点击切换买入/卖出"
                      >
                        {badge?.label ?? (isBuy ? "买入" : "卖出")}
                      </button>
                      {tx.in_progress ? (
                        <span className="text-[12px] font-medium text-[var(--warn-icon)]">交易进行中</span>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setSearchIndex(null);
                        setEditingIndex(index);
                      }}
                      aria-label={`修改交易：${rowLabel}`}
                      className="flex min-h-11 w-full items-start justify-between gap-3 text-left"
                    >
                      <span className="min-w-0 text-[16px] font-bold leading-6 text-slate-900">
                        {tx.fund_name || "未识别基金"}
                      </span>
                      <span className="shrink-0 text-[16px] font-bold tabular-nums leading-6 text-slate-900">
                        {formatPlainMoney(tx.amount_yuan)} 元
                      </span>
                    </button>
                    <p className="mt-0.5 text-[13px] leading-5 text-slate-400">{tx.trade_time}</p>
                    {unresolved ? (
                      <button
                        ref={(node) => {
                          searchTriggerRefs.current[index] = node;
                        }}
                        type="button"
                        onClick={() => openSearch(index)}
                        className="mt-1 inline-flex min-h-11 items-center gap-1 text-[13px] font-medium text-[var(--warn-fg)]"
                      >
                        <Search size={12} />
                        请选择基金
                      </button>
                    ) : tx.match_source === "similar" ? (
                      <p className="mt-1 text-[11px] leading-4 text-slate-500">请确认基金</p>
                    ) : null}
                    {searchIndex === index ? (
                      <FundCodeSearchPanel
                        initialQuery={searchQuery || tx.fund_name}
                        onSelect={(item) => applySearchedFund(index, item)}
                        onClose={() => closeSearch(index)}
                      />
                    ) : null}
                  </div>
                )}
              </div>
            );
          })}
          </div>

          {onContinueUpload || onUploadMore ? (
            <button
              type="button"
              onClick={handleContinueUpload}
              disabled={locked}
              className="mb-2 mt-3 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-[var(--info-border)] bg-[var(--panel)] py-3 text-sm font-bold text-[var(--brand)] transition hover:bg-[var(--info-bg)] disabled:opacity-50"
            >
              <Plus size={15} />
              继续上传
            </button>
          ) : null}
        </div>

        <div className="border-t border-slate-100 px-4 pb-[max(1rem,env(safe-area-inset-bottom,0px))] pt-4">
          {onUploadMore ? (
            <input
              ref={continueFileRef}
              type="file"
              accept="image/*"
              multiple
              className="sr-only"
              tabIndex={-1}
              disabled={locked}
              aria-label="继续选择交易记录截图"
              onChange={(event) => {
                const { files } = collectImageFiles(event.target.files);
                if (files.length) {
                  onUploadMore(files);
                }
                event.currentTarget.value = "";
              }}
            />
          ) : null}
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
          <button
            type="button"
            disabled={locked || validCount === 0}
            onClick={() => {
              if (applyCount === 0) {
                onConfirm("markers_only");
                return;
              }
              setPlanOpen(true);
            }}
            className="btn-primary w-full rounded-2xl disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isUploading
              ? ocrProgressLabel(true, uploadProgress, "识别中...")
              : isBusy
                ? "正在应用..."
                : applyCount > 0
                  ? `完成（${applyCount}）`
                  : validCount > 0
                    ? "完成（全部已录入）"
                    : "确认写入（0）"}
          </button>
        </div>
      </div>
      {planOpen ? (
        <TransactionSyncPlanDialog
          value={syncPlan}
          busy={isBusy}
          onChange={setSyncPlan}
          onCancel={() => setPlanOpen(false)}
          onConfirm={() => {
            setPlanOpen(false);
            onConfirm(syncPlan);
          }}
        />
      ) : null}
    </div>
  );
}

const SYNC_PLAN_OPTIONS: {
  value: TransactionSyncPlan;
  title: string;
}[] = [
  {
    value: "apply_position",
    title: "同步买卖点且进行加减仓操作",
  },
  {
    value: "markers_only",
    title: "仅同步买卖点，不进行加减仓",
  },
];

function TransactionSyncPlanDialog({
  value,
  busy,
  onChange,
  onCancel,
  onConfirm,
}: {
  value: TransactionSyncPlan;
  busy: boolean;
  onChange: (value: TransactionSyncPlan) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose: busy ? () => undefined : onCancel,
    initialFocusRef: confirmButtonRef,
  });

  return (
    <div
      className="modal-backdrop fixed inset-0 z-[60] flex items-center justify-center p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) {
          onCancel();
        }
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="modal-sheet w-full max-w-sm overflow-hidden rounded-[var(--radius-card)] px-5 pb-5 pt-6"
        role="dialog"
        aria-modal="true"
        aria-labelledby="transaction-sync-plan-title"
      >
        <h3
          id="transaction-sync-plan-title"
          className="text-center text-base font-black text-[var(--brand-deep)]"
        >
          请选择同步方案
        </h3>
        <div
          className="mt-5 space-y-3"
          role="radiogroup"
          aria-labelledby="transaction-sync-plan-title"
        >
          {SYNC_PLAN_OPTIONS.map((option) => {
            const selected = value === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={busy}
                onClick={() => onChange(option.value)}
                className={`flex w-full items-center gap-3 rounded-2xl border px-3.5 py-3 text-left transition ${
                  selected
                    ? "border-[var(--success-border)] bg-[var(--success-bg)]/60"
                    : "border-slate-200 bg-slate-50/80 hover:border-slate-300"
                }`}
              >
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${
                    selected
                      ? "border-[var(--success-icon)] bg-[var(--success-icon)] text-white"
                      : "border-slate-300 bg-white"
                  }`}
                  aria-hidden
                >
                  {selected ? <Check size={12} strokeWidth={3} /> : null}
                </span>
                <span className="block text-sm font-bold leading-5 text-slate-900">
                  {option.title}
                </span>
              </button>
            );
          })}
        </div>
        <button
          ref={confirmButtonRef}
          type="button"
          disabled={busy}
          onClick={onConfirm}
          className="btn-primary mt-5 w-full rounded-2xl disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "正在应用..." : "确定"}
        </button>
      </div>
    </div>
  );
}
