"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import type { FundSearchItem, Holding } from "@/lib/api";
import { searchFunds } from "@/lib/api";
import { cnProfitClass } from "@/lib/holdingMetrics";
import { useDialogA11y } from "@/lib/useDialogA11y";

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
  amountSemanticsNote?: string | null;
  ocrSource?: string | null;
  isBusy?: boolean;
  errorMessage?: string | null;
  onChange: (holdings: Holding[]) => void;
  onConfirm: () => void;
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

export function AlipayOcrConfirmModal({
  holdings,
  fundCodeResolutions = [],
  amountSemanticsNote,
  isBusy = false,
  errorMessage = null,
  onChange,
  onConfirm,
  onClose,
}: AlipayOcrConfirmModalProps) {
  const resolutionByName = useMemo(
    () => new Map(fundCodeResolutions.map((item) => [item.fund_name, item])),
    [fundCodeResolutions],
  );
  const [searchIndex, setSearchIndex] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
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
  const unresolvedCount = holdings.filter((holding) => {
    const resolution = resolutionByName.get(holding.fund_name);
    const code = displayCode(holding, resolution);
    return !code;
  }).length;
  const autoOpenedSearchRef = useRef(false);

  useEffect(() => {
    if (autoOpenedSearchRef.current || isBusy || searchIndex !== null) {
      return;
    }
    const firstUnresolved = holdings.findIndex((holding) => {
      const resolution = resolutionByName.get(holding.fund_name);
      return !displayCode(holding, resolution);
    });
    if (firstUnresolved < 0) {
      return;
    }
    autoOpenedSearchRef.current = true;
    setSearchIndex(firstUnresolved);
    setSearchQuery(holdings[firstUnresolved]?.fund_name ?? "");
  }, [holdings, resolutionByName, isBusy, searchIndex]);

  const removeAt = (index: number) => {
    onChange(holdings.filter((_, itemIndex) => itemIndex !== index));
  };

  const updateAt = (index: number, patch: Partial<Holding>) => {
    onChange(holdings.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  };

  const openSearch = (index: number) => {
    setSearchIndex(index);
    setSearchQuery(holdings[index]?.fund_name ?? "");
  };

  const closeSearch = (index: number | null = searchIndex) => {
    setSearchIndex(null);
    if (index != null) {
      window.requestAnimationFrame(() => searchTriggerRefs.current[index]?.focus());
    }
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
        aria-busy={isBusy}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 id="ocr-confirm-modal-title" className="text-lg font-black text-slate-950">
              确认识别结果
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              可修改基金代码、名称、金额与收益；代码不对时点搜索从东财选取。
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

        <ol className="workflow-rail" aria-label="持仓导入进度">
          <li className="is-done"><span>01</span><strong>截图进入</strong></li>
          <li aria-current="step"><span>02</span><strong>校对数据</strong></li>
          <li><span>03</span><strong>确认写入</strong></li>
        </ol>

        {errorMessage ? (
          <div className="px-4 pt-4">
            <InlineNotice tone="error" message={errorMessage} />
          </div>
        ) : null}

        {amountSemanticsNote ? (
          <div className="border-b border-blue-100 bg-blue-50 px-5 py-3 text-xs leading-5 text-blue-800">
            {amountSemanticsNote}
          </div>
        ) : null}

        {unresolvedCount > 0 ? (
          <div className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-xs leading-5 text-amber-900">
            有 {unresolvedCount} 只基金未自动匹配到代码，请逐一点「搜索」从东财基金库选取后再确认入库。
          </div>
        ) : null}

        <div className="ocr-review-list min-h-0 flex-1 overflow-y-auto px-4 py-2 sm:px-5">
          {holdings.map((holding, index) => {
            const resolution = resolutionByName.get(holding.fund_name);
            const code = displayCode(holding, resolution);
            const unresolved = !code;

            return (
              <div
                key={`${holding.fund_name}-${index}`}
                className={`ocr-review-row border-b px-1 py-4 sm:px-2 ${
                  unresolved
                    ? "border-amber-300 bg-amber-50/50"
                    : "border-[var(--line)] bg-transparent"
                }`}
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="relative">
                      <div className="flex items-center gap-2">
                        <input
                          value={code}
                          inputMode="numeric"
                          aria-label={`基金代码：${holding.fund_name || `第 ${index + 1} 只基金`}`}
                          onChange={(event) => {
                            const next = event.target.value.replace(/\D/g, "").slice(0, 6);
                            updateAt(index, { fund_code: next || "000000" });
                          }}
                          placeholder="待匹配"
                          className={`min-h-11 w-24 rounded-lg border px-2 py-2 text-xs font-bold tabular-nums outline-none focus:border-blue-400 ${
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
                          onClick={() => openSearch(index)}
                          className={`inline-flex min-h-11 items-center gap-1 rounded-lg border px-2 py-2 text-[11px] font-semibold transition ${
                            unresolved
                              ? "border-amber-400 bg-amber-100 text-amber-900 hover:border-amber-500"
                              : "border-slate-200 bg-white text-slate-600 hover:border-blue-300 hover:text-blue-700"
                          }`}
                        >
                          <Search size={12} />
                          {unresolved ? "搜索匹配" : "搜索"}
                        </button>
                        {resolution?.source ? (
                          <span className="text-[10px] text-slate-500">
                            {resolution.source === "fuzzy" ? "模糊匹配" : resolution.source}
                          </span>
                        ) : null}
                      </div>
                      {unresolved && resolution?.message ? (
                        <p className="mt-1 text-[11px] leading-4 text-amber-700">{resolution.message}</p>
                      ) : null}
                      {searchIndex === index ? (
                        <FundCodeSearchPanel
                          initialQuery={searchQuery}
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
                    <input
                      value={holding.fund_name}
                      aria-label={`基金名称：第 ${index + 1} 只基金`}
                      onChange={(event) => updateAt(index, { fund_name: event.target.value })}
                      className="min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-sm font-black text-slate-950 outline-none focus:border-blue-400"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => removeAt(index)}
                    className="touch-target inline-flex shrink-0 items-center justify-center rounded-full text-slate-500 transition hover:bg-white hover:text-rose-600"
                    aria-label="移除"
                  >
                    <X size={16} />
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-[11px] font-semibold text-slate-500">持有金额</div>
                    <input
                      value={String(holding.holding_amount ?? 0)}
                      inputMode="decimal"
                      aria-label={`持有金额：${holding.fund_name || `第 ${index + 1} 只基金`}`}
                      onChange={(event) =>
                        updateAt(index, { holding_amount: parseAmountInput(event.target.value) })
                      }
                      className="mt-0.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 font-black tabular-nums text-slate-950 outline-none focus:border-blue-400"
                    />
                  </div>
                  <div>
                    <div className="text-[11px] font-semibold text-slate-500">持有收益</div>
                    <input
                      value={
                        holding.holding_profit === null || holding.holding_profit === undefined
                          ? ""
                          : String(holding.holding_profit)
                      }
                      inputMode="decimal"
                      aria-label={`持有收益：${holding.fund_name || `第 ${index + 1} 只基金`}`}
                      onChange={(event) =>
                        updateAt(index, { holding_profit: parseProfitInput(event.target.value) })
                      }
                      className={`mt-0.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-right font-black tabular-nums outline-none focus:border-blue-400 ${cnProfitClass(holding.holding_profit)}`}
                    />
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="border-t border-slate-100 px-4 py-4">
          <button
            type="button"
            disabled={isBusy || holdings.length === 0}
            onClick={onConfirm}
            className="btn-primary min-h-11 w-full px-4 py-3 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isBusy ? "正在更新..." : `完成（${holdings.length}）`}
          </button>
        </div>
      </div>
    </div>
  );
}
