"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronUp, Plus, X } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import { CLIPBOARD_IMAGE_PASTE_QUERY } from "@/components/ScreenshotIntakeExtras";
import { FundCodeSearchButton, ReviewEditRow } from "@/components/ocrReviewFields";
import type { FundSearchItem, Holding } from "@/lib/api";
import { searchFunds } from "@/lib/api";
import { cnProfitClass, formatPlainMoney, formatSignedMoney } from "@/lib/holdingMetrics";
import { collectImageFiles, ocrProgressLabel } from "@/lib/ocrBatchUpload";
import { useDialogA11y } from "@/lib/useDialogA11y";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { userFacingErrorMessage } from "@/lib/userFacingError";

type FundCodeResolution = {
  fund_name: string;
  fund_code: string | null;
  source: string | null;
  resolved: boolean;
  message?: string | null;
};

type AlipayOcrConfirmModalProps = {
  holdings: Holding[];
  fundCodeResolutions?: FundCodeResolution[];
  ocrSource?: string | null;
  isBusy?: boolean;
  isUploading?: boolean;
  uploadProgress?: { current: number; total: number } | null;
  errorMessage?: string | null;
  onChange: (holdings: Holding[]) => void;
  onConfirm: () => void;
  onContinueUpload?: () => void;
  onUploadMore?: (files: File[]) => void;
  onClose: () => void;
};

function parseAmountInput(value: string): number {
  const parsed = Number.parseFloat(value.replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseProfitInput(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number.parseFloat(trimmed.replace(/,/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function displayCode(holding: Holding, resolution?: FundCodeResolution) {
  if (holding.fund_code && holding.fund_code !== "000000") {
    return holding.fund_code;
  }
  return resolution?.fund_code ?? "";
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

export function AlipayOcrConfirmModal({
  holdings,
  fundCodeResolutions = [],
  isBusy = false,
  isUploading = false,
  uploadProgress = null,
  errorMessage = null,
  onChange,
  onConfirm,
  onContinueUpload,
  onUploadMore,
  onClose,
}: AlipayOcrConfirmModalProps) {
  const resolutionByName = useMemo(
    () => new Map(fundCodeResolutions.map((item) => [item.fund_name, item])),
    [fundCodeResolutions],
  );
  const [searchIndex, setSearchIndex] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
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
    closeOnEscape: searchIndex === null,
  });
  const unresolvedCount = holdings.filter((holding) => {
    const resolution = resolutionByName.get(holding.fund_name);
    const code = displayCode(holding, resolution);
    return !code;
  }).length;

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

  const removeAt = (index: number) => {
    if (editingIndex === index) {
      setEditingIndex(null);
    }
    if (searchIndex === index) {
      setSearchIndex(null);
    }
    onChange(holdings.filter((_, itemIndex) => itemIndex !== index));
  };

  const updateAt = (index: number, patch: Partial<Holding>) => {
    onChange(holdings.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  };

  const openSearch = (index: number) => {
    setSearchIndex(index);
    setSearchQuery(holdings[index]?.fund_name || holdings[index]?.fund_code || "");
  };

  const closeSearch = (index: number | null = searchIndex) => {
    setSearchIndex(null);
    if (index != null) {
      window.requestAnimationFrame(() => searchTriggerRefs.current[index]?.focus());
    }
  };

  const applySearchedFund = (index: number, item: FundSearchItem) => {
    updateAt(index, {
      fund_code: item.fund_code,
      fund_name: item.fund_name,
    });
    closeSearch(index);
  };

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
        className="workflow-dialog flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-[18px] bg-[var(--panel)] shadow-[var(--shadow-lg)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ocr-confirm-modal-title"
        aria-busy={locked}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 id="ocr-confirm-modal-title" className="text-lg font-black text-slate-950">
              确认识别结果
            </h2>
          </div>
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

        <div className="ocr-review-list min-h-0 flex-1 overflow-y-auto bg-[#f5f7fa] px-4 py-3">
          <div className="space-y-2">
          {holdings.map((holding, index) => {
            const resolution = resolutionByName.get(holding.fund_name);
            const code = displayCode(holding, resolution);
            const unresolved = !code;
            const editing = editingIndex === index;
            const searching = searchIndex === index;
            const confirmHint =
              unresolved ||
              resolution?.source === "similar" ||
              resolution?.message === "请确认基金";
            const rowLabel = holding.fund_name || `第 ${index + 1} 只基金`;

            return (
              <div
                key={`${holding.fund_name}-${index}`}
                className={`ocr-review-row relative rounded-2xl bg-white px-4 py-3 shadow-[0_2px_12px_rgba(15,23,42,0.06)] ${
                  unresolved ? "ring-1 ring-[var(--warn-border)]" : ""
                }`}
              >
                <button
                  type="button"
                  onClick={() => removeAt(index)}
                  className="touch-target absolute right-1.5 top-1.5 z-10 inline-flex items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-[var(--danger-icon)]"
                  aria-label="移除"
                >
                  <X size={16} />
                </button>

                {editing ? (
                  <div className="relative pr-6">
                    <FundCodeSearchButton
                      code={code}
                      fundName={holding.fund_name}
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
                      value={holding.fund_name}
                      ariaLabel={`基金名称：${rowLabel}`}
                      onChange={(value) => updateAt(index, { fund_name: value })}
                    />
                    <ReviewEditRow
                      label="持有金额"
                      value={String(holding.holding_amount ?? 0)}
                      ariaLabel={`持有金额：${rowLabel}`}
                      inputMode="decimal"
                      onChange={(value) =>
                        updateAt(index, { holding_amount: parseAmountInput(value) })
                      }
                    />
                    <ReviewEditRow
                      label="持有收益"
                      value={
                        holding.holding_profit === null || holding.holding_profit === undefined
                          ? ""
                          : String(holding.holding_profit)
                      }
                      ariaLabel={`持有收益：${rowLabel}`}
                      inputMode="decimal"
                      className={`tabular-nums ${cnProfitClass(holding.holding_profit)}`}
                      onChange={(value) =>
                        updateAt(index, { holding_profit: parseProfitInput(value) })
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
                    <div className="grid grid-cols-[minmax(0,1fr)_5.75rem_5.25rem] items-center gap-x-3 gap-y-0.5">
                      <FundCodeSearchButton
                        code={code}
                        fundName={holding.fund_name}
                        unresolved={unresolved}
                        buttonRef={(node) => {
                          searchTriggerRefs.current[index] = node;
                        }}
                        onClick={() => openSearch(index)}
                      />
                      <p className="text-right text-[10px] font-semibold text-slate-500">持有金额</p>
                      <p className="text-right text-[10px] font-semibold text-slate-500">持有收益</p>
                      <button
                        type="button"
                        onClick={() => {
                          setSearchIndex(null);
                          setEditingIndex(index);
                        }}
                        aria-label={`修改持仓：${rowLabel}`}
                        className="col-span-3 grid min-h-[44px] grid-cols-[minmax(0,1fr)_5.75rem_5.25rem] items-center gap-x-3 text-left"
                      >
                        <span className="line-clamp-2 text-sm font-bold leading-5 text-slate-900">
                          {holding.fund_name || "未识别基金"}
                        </span>
                        <span className="text-right text-sm font-bold tabular-nums text-slate-900">
                          {formatPlainMoney(holding.holding_amount)}
                        </span>
                        <span
                          className={`text-right text-sm font-bold tabular-nums ${cnProfitClass(holding.holding_profit)}`}
                        >
                          {formatSignedMoney(holding.holding_profit)}
                        </span>
                      </button>
                    </div>
                    {confirmHint ? (
                      <p className="mt-1 text-[11px] leading-4 text-slate-500">请确认基金</p>
                    ) : null}
                    {searching ? (
                      <FundCodeSearchPanel
                        initialQuery={searchQuery}
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
              className="mb-2 mt-3 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-[var(--info-border)] bg-white py-3 text-sm font-bold text-blue-600 transition hover:bg-[var(--info-bg)] disabled:opacity-50"
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
              aria-label="继续选择持仓截图"
              onChange={(event) => {
                const { files } = collectImageFiles(event.target.files);
                if (files.length) {
                  onUploadMore(files);
                }
                event.currentTarget.value = "";
              }}
            />
          ) : null}
          <button
            type="button"
            disabled={locked || holdings.length === 0 || unresolvedCount > 0}
            onClick={onConfirm}
            className="btn-primary min-h-11 w-full px-4 py-3 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isUploading
              ? ocrProgressLabel(true, uploadProgress, "识别中...")
              : isBusy
                ? "正在更新..."
                : unresolvedCount > 0
                  ? `请先补全基金代码（${unresolvedCount}）`
                  : `完成（${holdings.length}）`}
          </button>
        </div>
      </div>
    </div>
  );
}
