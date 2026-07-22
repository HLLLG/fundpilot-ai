"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import type { FundNavPoint } from "@/lib/api";
import { fetchFundNavHistoryPage } from "@/lib/api";
import { cnSignedPercent, formatSignedPercent } from "@/lib/performanceTrend";
import { useDialogA11y } from "@/lib/useDialogA11y";

type NavHistoryListModalProps = {
  fundCode: string;
  fundName: string;
  onClose: () => void;
};

function toDisplayPoint(point: FundNavPoint) {
  return {
    date: point.date.slice(0, 10),
    nav: point.nav,
    dailyReturn: point.daily_return_percent ?? null,
  };
}

export function NavHistoryListModal({ fundCode, fundName, onClose }: NavHistoryListModalProps) {
  const [rows, setRows] = useState<Array<ReturnType<typeof toDisplayPoint>>>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextBeforeRef = useRef<string | null>(null);
  const loadingMoreRef = useRef(false);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose,
    initialFocusRef: closeButtonRef,
  });

  const loadPage = useCallback(
    async (before: string | null, append: boolean) => {
      if (loadingMoreRef.current) {
        return;
      }
      loadingMoreRef.current = true;
      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
      }
      setError(null);
      try {
        const result = await fetchFundNavHistoryPage(fundCode, {
          limit: 30,
          before,
        });
        const mapped = result.points.map(toDisplayPoint);
        setRows((current) => (append ? [...current, ...mapped] : mapped));
        setHasMore(result.has_more);
        nextBeforeRef.current = result.next_before ?? null;
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "加载历史净值失败");
        if (!append) {
          setRows([]);
          setHasMore(false);
        }
      } finally {
        loadingMoreRef.current = false;
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [fundCode],
  );

  useEffect(() => {
    void loadPage(null, false);
  }, [loadPage]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    const root = listRef.current;
    if (!sentinel || !root) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries[0]?.isIntersecting || !hasMore || loadingMoreRef.current) {
          return;
        }
        void loadPage(nextBeforeRef.current, true);
      },
      { root, rootMargin: "120px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadPage, rows.length]);

  return (
    <div
      className="fixed inset-0 z-[85] flex items-end justify-center bg-slate-950/45 p-0 sm:items-center sm:p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl sm:rounded-2xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="nav-history-title"
        aria-describedby="nav-history-fund-name"
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <div className="min-w-0">
            <h2 id="nav-history-title" className="truncate text-base font-bold text-slate-900">
              历史净值
            </h2>
            <p id="nav-history-fund-name" className="truncate text-xs text-slate-500">
              {fundName}
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="touch-target inline-flex items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold text-slate-500">
          <span>日期</span>
          <span className="text-center">净值</span>
          <span className="text-right">日涨幅</span>
        </div>

        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-sm text-slate-500" role="status">
              <Loader2 size={18} className="mr-2 animate-spin" />
              加载中…
            </div>
          ) : error && rows.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-[var(--danger-icon)]" role="alert">
              {error}
            </div>
          ) : rows.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-slate-500">暂无历史净值</div>
          ) : (
            <>
              {rows.map((row) => (
                <div
                  key={row.date}
                  className="grid grid-cols-3 border-b border-slate-50 px-4 py-2.5 text-[13px] tabular-nums"
                >
                  <span className="text-slate-600">{row.date}</span>
                  <span className="text-center font-semibold text-slate-900">{row.nav.toFixed(4)}</span>
                  <span className={`text-right font-bold ${cnSignedPercent(row.dailyReturn)}`}>
                    {formatSignedPercent(row.dailyReturn)}
                  </span>
                </div>
              ))}
              <div ref={sentinelRef} className="py-4 text-center text-xs text-slate-500">
                {loadingMore ? (
                  <span className="inline-flex items-center gap-2">
                    <Loader2 size={14} className="animate-spin" />
                    加载更多…
                  </span>
                ) : hasMore ? (
                  "上滑加载更多"
                ) : (
                  "已加载全部"
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
