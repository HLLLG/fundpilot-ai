"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronRight, History, RefreshCw, Trash2, X } from "lucide-react";
import type { FundDiscoveryReport } from "@/lib/api";
import { deleteDiscoveryReport } from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";
import { useDialogA11y } from "@/lib/useDialogA11y";

type DiscoveryHistoryRailProps = {
  reports: FundDiscoveryReport[];
  activeReportId?: string | null;
  onRefresh: () => void;
  onSelect: (report: FundDiscoveryReport) => void;
  onDeleted?: (reportId: string) => void;
  variant?: "rail" | "drawer";
  onOpenAll?: () => void;
  initialLimit?: number;
};

type DeleteIntent =
  | { kind: "single"; reports: [FundDiscoveryReport] }
  | { kind: "batch"; reports: FundDiscoveryReport[] };

export function DiscoveryHistoryRail({
  reports,
  activeReportId,
  onRefresh,
  onSelect,
  onDeleted,
  variant = "rail",
  onOpenAll,
  initialLimit = 12,
}: DiscoveryHistoryRailProps) {
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [deleteIntent, setDeleteIntent] = useState<DeleteIntent | null>(null);
  const [deleteFeedback, setDeleteFeedback] = useState<{
    message: string;
    tone: "error" | "warning";
  } | null>(null);
  const cancelDeleteButtonRef = useRef<HTMLButtonElement>(null);
  const [visibleCount, setVisibleCount] = useState(initialLimit);
  const [query, setQuery] = useState("");
  const deleteDialogRef = useDialogA11y<HTMLDivElement>({
    open: deleteIntent != null,
    onClose: () => setDeleteIntent(null),
    initialFocusRef: cancelDeleteButtonRef,
  });

  const selectedCount = selectedIds.size;
  const allSelected = reports.length > 0 && selectedCount === reports.length;
  const filteredReports = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase("zh-CN");
    if (!keyword) return reports;
    return reports.filter((item) =>
      [item.title, ...(item.target_sectors ?? []), new Date(item.created_at).toLocaleDateString("zh-CN")]
        .join(" ")
        .toLocaleLowerCase("zh-CN")
        .includes(keyword),
    );
  }, [query, reports]);
  const visibleReports = useMemo(() => {
    if (batchMode) return filteredReports;
    const visible = filteredReports.slice(0, visibleCount);
    const active = filteredReports.find((item) => item.id === activeReportId);
    if (active && !visible.some((item) => item.id === active.id)) visible.push(active);
    return visible;
  }, [activeReportId, batchMode, filteredReports, visibleCount]);
  const hasMore = !batchMode && visibleCount < filteredReports.length;

  useEffect(() => {
    setVisibleCount(initialLimit);
  }, [initialLimit, query, reports.length]);

  const exitBatchMode = () => {
    setBatchMode(false);
    setSelectedIds(new Set());
  };

  const toggleSelected = (reportId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(reportId)) {
        next.delete(reportId);
      } else {
        next.add(reportId);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(new Set(reports.map((report) => report.id)));
  };

  const requestBatchDelete = () => {
    if (selectedCount === 0) {
      return;
    }
    const selectedReports = reports.filter((report) => selectedIds.has(report.id));
    if (selectedReports.length === 0) {
      return;
    }
    setDeleteIntent({ kind: "batch", reports: selectedReports });
  };

  const confirmDelete = async () => {
    const intent = deleteIntent;
    if (!intent) {
      return;
    }
    setDeleteFeedback(null);
    setDeleteIntent(null);

    if (intent.kind === "single") {
      const report = intent.reports[0];
      setDeletingId(report.id);
      try {
        await deleteDiscoveryReport(report.id);
        onDeleted?.(report.id);
        await onRefresh();
      } catch {
        setDeleteFeedback({ message: "删除失败，请稍后重试。", tone: "error" });
      } finally {
        setDeletingId(null);
      }
      return;
    }

    setBatchDeleting(true);
    try {
      const selectedIdList = intent.reports.map((report) => report.id);
      const results = await Promise.allSettled(
        selectedIdList.map((id) => deleteDiscoveryReport(id)),
      );
      const failed = results.filter((result) => result.status === "rejected").length;
      const succeededIds = selectedIdList.filter(
        (_, index) => results[index].status === "fulfilled",
      );
      for (const id of succeededIds) {
        onDeleted?.(id);
      }
      await onRefresh();
      exitBatchMode();
      if (failed > 0) {
        setDeleteFeedback({
          message: `${failed} 份删除失败，其余已删除。可重新选择失败项后重试。`,
          tone: "warning",
        });
      }
    } catch {
      setDeleteFeedback({ message: "批量删除失败，请稍后重试。", tone: "error" });
    } finally {
      setBatchDeleting(false);
    }
  };

  const deleteCount = deleteIntent?.reports.length ?? 0;
  const deletePreview = deleteIntent?.reports.slice(0, 3) ?? [];

  return (
    <aside className={`discovery-history-rail min-w-0 ${variant === "drawer" ? "is-drawer" : "is-rail"}`}>
      <div className="discovery-history-toolbar">
        <div className="flex items-center gap-2 font-black text-slate-950">
          <History size={18} />
          <span>历史推荐</span>
          <span className="history-count" aria-label={`共 ${reports.length} 份历史推荐`}>
            {reports.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {batchMode ? (
            <>
              <button
                type="button"
                onClick={toggleSelectAll}
                disabled={batchDeleting || reports.length === 0}
                className="min-h-11 rounded-full border border-slate-200 bg-white px-3 text-xs font-bold text-slate-600 transition hover:border-[rgba(37,99,235,0.4)] hover:text-[var(--brand-strong)] disabled:opacity-50"
              >
                {allSelected ? "取消全选" : "全选"}
              </button>
              <button
                type="button"
                onClick={requestBatchDelete}
                disabled={batchDeleting || selectedCount === 0}
                className="inline-flex min-h-11 items-center gap-1 rounded-full bg-rose-600 px-3 text-xs font-bold text-white transition hover:bg-rose-700 disabled:opacity-50"
              >
                <Trash2 size={12} />
                {batchDeleting ? "删除中…" : `删除(${selectedCount})`}
              </button>
              <button
                type="button"
                onClick={exitBatchMode}
                disabled={batchDeleting}
                className="touch-target inline-flex items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-slate-800 disabled:opacity-50"
                aria-label="退出批量删除"
              >
                <X size={16} />
              </button>
            </>
          ) : (
            <>
              {reports.length > 0 ? (
                <button
                  type="button"
                  onClick={() => setBatchMode(true)}
                  className="min-h-11 rounded-full border border-slate-200 bg-white px-3 text-xs font-bold text-slate-600 transition hover:border-rose-300 hover:text-rose-700"
                >
                  管理
                </button>
              ) : null}
              <button
                type="button"
                onClick={onRefresh}
                className="touch-target inline-flex items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-[var(--brand)]"
                aria-label="刷新历史推荐"
              >
                <RefreshCw size={16} />
              </button>
            </>
          )}
        </div>
      </div>
      {deleteFeedback ? (
        <InlineNotice
          tone={deleteFeedback.tone}
          message={deleteFeedback.message}
          className="mb-3"
        />
      ) : null}
      {variant === "drawer" && !batchMode && reports.length > 0 ? (
        <label className="history-search-field">
          <span className="sr-only">搜索历史推荐</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索标题、板块或日期"
            className="min-h-11"
          />
        </label>
      ) : null}
      <div
        className="history-scroll-region"
        data-testid="discovery-history-scroll-region"
        aria-label="历史推荐列表"
      >
        {reports.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-500">
            完成首次扫描后会自动保存到这里。
          </div>
        ) : null}
        {reports.length > 0 && filteredReports.length === 0 ? (
          <div className="history-empty-search">没有匹配的历史推荐，请换一个关键词。</div>
        ) : null}
        {visibleReports.map((item) => {
          const active = item.id === activeReportId;
          const selected = selectedIds.has(item.id);
          return (
            <div
              key={item.id}
              data-testid="discovery-history-item"
              className={`flex items-stretch gap-2 rounded-2xl p-2 shadow-sm transition ${
                batchMode
                  ? selected
                    ? "ring-2 ring-rose-200"
                    : "bg-white"
                  : active
                    ? "bg-[var(--brand-soft)] ring-1 ring-[rgba(37,99,235,0.25)]"
                    : "bg-white hover:-translate-y-0.5 hover:shadow-md"
              }`}
            >
              {batchMode ? (
                <label className="touch-target flex shrink-0 cursor-pointer items-center justify-center">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={batchDeleting}
                    onChange={() => toggleSelected(item.id)}
                    className="h-4 w-4 rounded border-slate-300 text-rose-600 focus:ring-rose-300"
                    aria-label={`选择推荐报告 ${item.title}`}
                  />
                </label>
              ) : null}
              <button
                type="button"
                aria-current={!batchMode && active ? "true" : undefined}
                aria-pressed={!batchMode ? active : undefined}
                onClick={() => {
                  if (batchMode) {
                    toggleSelected(item.id);
                    return;
                  }
                  onSelect(item);
                }}
                disabled={batchDeleting}
                className="min-h-11 min-w-0 flex-1 rounded-xl px-3 py-2 text-left disabled:opacity-60"
              >
                <div className="line-clamp-2 text-sm font-black text-slate-950">{item.title}</div>
                <div className="mt-1 text-xs text-slate-500">
                  {new Date(item.created_at).toLocaleString("zh-CN")}
                </div>
                {item.target_sectors?.length ? (
                  <div className="mt-1 line-clamp-1 text-[11px] text-slate-500">
                    {item.target_sectors.join("、")}
                  </div>
                ) : null}
              </button>
              {!batchMode ? (
                <button
                  type="button"
                  disabled={deletingId === item.id}
                  aria-label={`删除推荐报告 ${item.title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    setDeleteIntent({ kind: "single", reports: [item] });
                  }}
                  className="inline-flex min-h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-500 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                >
                  <Trash2 size={16} />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
      {!batchMode && reports.length > 0 ? (
        <div className="history-rail-footer">
          {hasMore ? (
            <button
              type="button"
              onClick={() => setVisibleCount((count) => Math.min(count + initialLimit, filteredReports.length))}
              className="btn-ghost min-h-11 flex-1 text-xs"
            >
              再显示 {Math.min(initialLimit, filteredReports.length - visibleCount)} 份
            </button>
          ) : null}
          {variant === "rail" && onOpenAll ? (
            <button type="button" onClick={onOpenAll} className="history-open-all min-h-11">
              全部历史
              <ChevronRight size={15} />
            </button>
          ) : null}
        </div>
      ) : null}
      {deleteIntent ? (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/45 p-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setDeleteIntent(null);
            }
          }}
          role="presentation"
        >
          <div
            ref={deleteDialogRef}
            tabIndex={-1}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="discovery-history-delete-title"
            aria-describedby="discovery-history-delete-description"
            className="w-full max-w-sm rounded-[24px] bg-white p-5 shadow-2xl"
          >
            <div className="flex items-start gap-3">
              <span
                className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-rose-50 text-rose-600"
                aria-hidden="true"
              >
                <AlertTriangle size={20} />
              </span>
              <div className="min-w-0">
                <h2
                  id="discovery-history-delete-title"
                  className="text-base font-black text-slate-950"
                >
                  {deleteIntent.kind === "batch"
                    ? `删除选中的 ${deleteCount} 份推荐报告？`
                    : "删除这份推荐报告？"}
                </h2>
                <p
                  id="discovery-history-delete-description"
                  className="mt-1 text-sm leading-6 text-slate-600"
                >
                  删除后无法恢复，请确认以下推荐报告不再需要。
                </p>
              </div>
            </div>

            <ul className="mt-4 space-y-2 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
              {deletePreview.map((report) => (
                <li key={report.id} className="truncate">
                  {report.title}
                </li>
              ))}
              {deleteCount > deletePreview.length ? (
                <li className="text-xs font-semibold text-slate-500">
                  以及另外 {deleteCount - deletePreview.length} 份推荐报告
                </li>
              ) : null}
            </ul>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <button
                ref={cancelDeleteButtonRef}
                type="button"
                onClick={() => setDeleteIntent(null)}
                className="btn-secondary min-h-11"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-rose-700"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
