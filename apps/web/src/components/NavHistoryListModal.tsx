"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import type { FundNavPoint } from "@/lib/api";
import { fetchFundNavHistoryPage } from "@/lib/api";
import { cnSignedPercent, formatSignedPercent } from "@/lib/performanceTrend";

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
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

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
        className="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl sm:rounded-2xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="nav-history-title"
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <div className="min-w-0">
            <h3 id="nav-history-title" className="truncate text-base font-bold text-slate-900">
              历史净值
            </h3>
            <p className="truncate text-xs text-slate-500">{fundName}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold text-slate-400">
          <span>日期</span>
          <span className="text-center">净值</span>
          <span className="text-right">日涨幅</span>
        </div>

        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-sm text-slate-400">
              <Loader2 size={18} className="mr-2 animate-spin" />
              加载中…
            </div>
          ) : error && rows.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-rose-600">{error}</div>
          ) : rows.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-slate-400">暂无历史净值</div>
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
              <div ref={sentinelRef} className="py-4 text-center text-xs text-slate-400">
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
