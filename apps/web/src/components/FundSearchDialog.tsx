"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Clock3, Loader2, Search, Trash2, X } from "lucide-react";
import { searchFundsPage, type FundSearchItem } from "@/lib/api";
import { useDialogA11y } from "@/lib/useDialogA11y";

type FundSearchDialogProps = {
  open: boolean;
  onClose: () => void;
  onSelect: (fund: FundSearchItem) => void;
};

type FundSearchHistoryItem = FundSearchItem & {
  searched_at: number;
};

const HISTORY_STORAGE_KEY = "fundpilot:fund-search-history:v1";
const HISTORY_LIMIT = 8;
const INITIAL_RESULT_LIMIT = 5;
const MORE_RESULT_LIMIT = 50;

function readSearchHistory(): FundSearchHistoryItem[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(HISTORY_STORAGE_KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is FundSearchHistoryItem =>
          Boolean(
            item &&
              typeof item === "object" &&
              typeof (item as FundSearchHistoryItem).fund_code === "string" &&
              typeof (item as FundSearchHistoryItem).fund_name === "string" &&
              typeof (item as FundSearchHistoryItem).searched_at === "number",
          ),
      )
      .slice(0, HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function saveSearchHistory(item: FundSearchItem): FundSearchHistoryItem[] {
  const next = [
    { ...item, searched_at: Date.now() },
    ...readSearchHistory().filter((history) => history.fund_code !== item.fund_code),
  ].slice(0, HISTORY_LIMIT);
  try {
    window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Private browsing or a full storage quota should not block fund selection.
  }
  return next;
}

function HighlightedName({ name, keyword }: { name: string; keyword: string }) {
  const index = keyword ? name.toLocaleLowerCase().indexOf(keyword.toLocaleLowerCase()) : -1;
  if (index < 0) return <>{name}</>;
  return (
    <>
      {name.slice(0, index)}
      <mark className="bg-transparent font-inherit text-orange-500">{name.slice(index, index + keyword.length)}</mark>
      {name.slice(index + keyword.length)}
    </>
  );
}

export function FundSearchDialog({ open, onClose, onSelect }: FundSearchDialogProps) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<FundSearchItem[]>([]);
  const [history, setHistory] = useState<FundSearchHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const queryRef = useRef("");
  const requestIdRef = useRef(0);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open,
    onClose,
    initialFocusRef: inputRef,
  });

  useEffect(() => {
    queryRef.current = query;
  }, [query]);

  useEffect(() => {
    if (open) {
      setHistory(readSearchHistory());
      return;
    }
    requestIdRef.current += 1;
    setQuery("");
    setItems([]);
    setTotal(0);
    setExpanded(false);
    setLoading(false);
    setLoadingMore(false);
    setError(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const keyword = query.trim();
    setExpanded(false);
    if (!keyword) {
      requestIdRef.current += 1;
      setItems([]);
      setTotal(0);
      setLoading(false);
      setLoadingMore(false);
      setError(null);
      return;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void searchFundsPage(keyword, INITIAL_RESULT_LIMIT, 0)
        .then((page) => {
          if (requestId === requestIdRef.current) {
            setItems(page.items);
            setTotal(page.total);
          }
        })
        .catch((reason) => {
          if (requestId === requestIdRef.current) {
            setItems([]);
            setTotal(0);
            setError(reason instanceof Error ? reason.message : "搜索失败，请稍后重试");
          }
        })
        .finally(() => {
          if (requestId === requestIdRef.current) setLoading(false);
        });
    }, 250);

    return () => window.clearTimeout(timer);
  }, [open, query]);

  if (!open) return null;

  const keyword = query.trim();
  const hasPopularItems = items.some((item) => item.popularity_rank != null);
  const remaining = Math.max(0, total - items.length);

  const selectFund = (item: FundSearchItem) => {
    setHistory(saveSearchHistory(item));
    onSelect(item);
  };

  const loadMore = () => {
    if (!keyword || loadingMore || remaining <= 0) return;
    setExpanded(true);
    setLoadingMore(true);
    setError(null);
    const offset = items.length;
    void searchFundsPage(keyword, MORE_RESULT_LIMIT, offset)
      .then((page) => {
        if (queryRef.current.trim() !== keyword) return;
        setItems((current) => {
          const seen = new Set(current.map((item) => item.fund_code));
          return [...current, ...page.items.filter((item) => !seen.has(item.fund_code))];
        });
        setTotal(page.total);
      })
      .catch((reason) => {
        if (queryRef.current.trim() === keyword) {
          setError(reason instanceof Error ? reason.message : "更多结果加载失败");
        }
      })
      .finally(() => {
        if (queryRef.current.trim() === keyword) setLoadingMore(false);
      });
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-slate-950/35 px-2 pt-4 backdrop-blur-[1px] sm:px-6 sm:pt-[9vh]"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fund-search-title"
        className="flex max-h-[88dvh] w-full max-w-xl flex-col overflow-hidden rounded-[24px] border border-white/70 bg-white shadow-[0_24px_70px_rgba(15,23,42,0.22)]"
      >
        <header className="flex items-center justify-between px-5 pb-2 pt-4 sm:px-6">
          <h2 id="fund-search-title" className="text-lg font-bold text-slate-950">
            搜索基金
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="touch-target inline-flex items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100"
            aria-label="关闭基金搜索"
          >
            <X size={20} />
          </button>
        </header>

        <div className="px-4 pb-4 pt-2 sm:px-6">
          <label htmlFor="global-fund-search" className="sr-only">
            输入基金名称或代码
          </label>
          <div className="flex min-h-12 items-center gap-3 rounded-xl border border-slate-200 bg-slate-100/80 px-3.5 transition focus-within:border-blue-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-100">
            {loading ? (
              <Loader2 size={19} className="shrink-0 animate-spin text-[var(--brand)]" />
            ) : (
              <Search size={19} className="shrink-0 text-slate-400" />
            )}
            <input
              ref={inputRef}
              id="global-fund-search"
              type="search"
              autoComplete="off"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="基金名称或代码"
              className="fund-search-input min-w-0 flex-1 appearance-none bg-transparent py-3 text-base text-slate-900 outline-none placeholder:text-slate-400 [&::-webkit-search-cancel-button]:hidden"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery("")}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-200"
                aria-label="清空搜索内容"
              >
                <X size={16} />
              </button>
            ) : null}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto border-t border-slate-100" aria-live="polite">
          {error && items.length === 0 ? (
            <div className="m-4 rounded-xl bg-rose-50 px-4 py-6 text-center text-sm text-rose-700">{error}</div>
          ) : !keyword ? (
            history.length ? (
              <section className="px-4 pb-4 pt-3 sm:px-6" aria-labelledby="fund-search-history-title">
                <div className="flex min-h-10 items-center justify-between">
                  <h3 id="fund-search-history-title" className="flex items-center gap-2 text-sm font-bold text-slate-800">
                    <Clock3 size={16} className="text-slate-400" />
                    最近搜索
                  </h3>
                  <button
                    type="button"
                    onClick={() => {
                      window.localStorage.removeItem(HISTORY_STORAGE_KEY);
                      setHistory([]);
                    }}
                    className="inline-flex min-h-10 items-center gap-1 rounded-lg px-2 text-xs text-slate-400 hover:bg-slate-50 hover:text-slate-700"
                  >
                    <Trash2 size={14} />
                    清空
                  </button>
                </div>
                <ul className="mt-1 divide-y divide-slate-100" aria-label="最近搜索基金">
                  {history.map((item) => (
                    <li key={item.fund_code}>
                      <button
                        type="button"
                        onClick={() => selectFund(item)}
                        className="group flex min-h-14 w-full items-center gap-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-100"
                        aria-label={`${item.fund_name} ${item.fund_code}`}
                      >
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-xs font-bold text-[var(--brand)]">基</span>
                        <span className="min-w-0 flex-1">
                          <strong className="block truncate text-sm font-semibold text-slate-900">{item.fund_name}</strong>
                          <span className="mt-0.5 block text-xs tabular-nums text-slate-400">{item.fund_code}</span>
                        </span>
                        <ArrowRight size={17} className="shrink-0 text-slate-300 group-hover:text-[var(--brand)]" />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : (
              <div className="flex min-h-36 flex-col items-center justify-center px-6 text-center text-sm text-slate-400">
                <Search size={26} className="mb-2 text-slate-300" />
                输入名称或代码查找基金
              </div>
            )
          ) : !loading && items.length === 0 ? (
            <div className="flex min-h-36 items-center justify-center px-6 text-center text-sm text-slate-500">
              未找到匹配基金
            </div>
          ) : (
            <section aria-labelledby="fund-search-results-title">
              <div className="flex min-h-11 items-center justify-between bg-slate-50/80 px-4 sm:px-6">
                <h3 id="fund-search-results-title" className="text-xs font-bold text-slate-600">
                  {expanded ? "全部匹配" : hasPopularItems ? "热门匹配" : "匹配基金"}
                </h3>
                <span className="text-xs tabular-nums text-slate-400">共 {total} 只</span>
              </div>
              <ul className="divide-y divide-slate-100 px-4 sm:px-6" aria-label="基金搜索结果">
                {items.map((item) => (
                  <li key={item.fund_code}>
                    <button
                      type="button"
                      onClick={() => selectFund(item)}
                      className="group flex min-h-[66px] w-full items-center gap-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-100"
                      aria-label={`${item.fund_name} ${item.fund_code}`}
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-xs font-bold text-[var(--brand)] group-hover:bg-blue-100">基</span>
                      <span className="min-w-0 flex-1">
                        <strong className="block truncate text-sm font-semibold text-slate-900">
                          <HighlightedName name={item.fund_name} keyword={keyword} />
                        </strong>
                        <span className="mt-1 flex min-w-0 items-center gap-2 text-xs text-slate-400">
                          <span className="tabular-nums">{item.fund_code}</span>
                          {item.fund_type ? <span className="truncate">{item.fund_type}</span> : null}
                        </span>
                      </span>
                      <ArrowRight size={17} className="shrink-0 text-slate-300 group-hover:text-[var(--brand)]" />
                    </button>
                  </li>
                ))}
              </ul>
              {remaining > 0 ? (
                <div className="border-t border-slate-100 p-3">
                  <button
                    type="button"
                    onClick={loadMore}
                    disabled={loadingMore}
                    className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl text-sm font-semibold text-[var(--brand)] transition hover:bg-blue-50 disabled:opacity-60"
                  >
                    {loadingMore ? <Loader2 size={16} className="animate-spin" /> : null}
                    {expanded ? `继续加载（剩余 ${remaining} 只）` : `更多匹配（${remaining} 只）`}
                  </button>
                </div>
              ) : null}
              {error ? <p className="px-4 pb-3 text-center text-xs text-rose-600">{error}</p> : null}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
