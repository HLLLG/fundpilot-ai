"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, History, RefreshCw, Trash2, X } from "lucide-react";
import type { Report } from "@/lib/api";
import { deleteReport } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";
import { InlineNotice } from "@/components/InlineNotice";
import { useDialogA11y } from "@/lib/useDialogA11y";

type HistoryRailProps = {
  reports: Report[];
  activeReportId?: string | null;
  onRefresh: () => void;
  onSelect: (report: Report) => void;
  onDeleted?: (reportId: string) => void;
  initialLimit?: number;
};

type DeleteIntent =
  | { kind: "single"; reports: [Report] }
  | { kind: "batch"; reports: Report[] };

function reportDateText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
}

export function HistoryRail({
  reports,
  activeReportId,
  onRefresh,
  onSelect,
  onDeleted,
  initialLimit = 20,
}: HistoryRailProps) {
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
      [item.title, reportDateText(item.created_at), item.risk.level]
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
    setDeleteIntent({
      kind: "batch",
      reports: selectedReports,
    });
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
        await deleteReport(report.id);
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
      const results = await Promise.allSettled(selectedIdList.map((id) => deleteReport(id)));
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
    <aside className="history-archive min-w-0 p-4 sm:p-5">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-black text-slate-950">
          <History size={18} />
          历史日报
          <span className="history-count" aria-label={`共 ${reports.length} 份历史日报`}>
            {reports.length}
          </span>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {batchMode ? (
            <>
              <button
                type="button"
                onClick={toggleSelectAll}
                disabled={batchDeleting || reports.length === 0}
                className="min-h-11 min-w-11 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 transition hover:border-blue-300 hover:text-blue-700 disabled:opacity-50"
              >
                {allSelected ? "取消全选" : "全选"}
              </button>
              <button
                type="button"
                onClick={requestBatchDelete}
                disabled={batchDeleting || selectedCount === 0}
                className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-full bg-rose-600 px-3 py-2 text-xs font-bold text-white transition hover:bg-rose-700 disabled:opacity-50"
              >
                <Trash2 size={12} />
                {batchDeleting ? "删除中…" : `删除(${selectedCount})`}
              </button>
              <button
                type="button"
                onClick={exitBatchMode}
                disabled={batchDeleting}
                className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-slate-800 disabled:opacity-50"
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
                  className="min-h-11 min-w-11 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 transition hover:border-rose-300 hover:text-rose-700"
                >
                  管理
                </button>
              ) : null}
              <button
                type="button"
                onClick={onRefresh}
                className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-blue-600"
                aria-label="刷新历史日报"
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
      {!batchMode && reports.length > 0 ? (
        <label className="history-search-field">
          <span className="sr-only">搜索历史日报</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索标题、日期或风险等级"
            className="min-h-11"
          />
        </label>
      ) : null}
      <div className="history-scroll-region" data-testid="report-history-scroll-region">
        {reports.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-500">
            生成第一份日报后会自动保存到这里。
          </div>
        ) : null}
        {reports.length > 0 && filteredReports.length === 0 ? (
          <div className="history-empty-search">没有匹配的历史日报，请换一个关键词。</div>
        ) : null}
        {visibleReports.map((report) => {
          const selected = selectedIds.has(report.id);
          const active = report.id === activeReportId;
          return (
            <div
              key={report.id}
              data-testid="report-history-item"
              className={`history-archive-row flex items-stretch gap-2 border-b border-[var(--line)] py-2 transition ${
                batchMode
                  ? selected
                    ? "ring-2 ring-rose-200"
                    : ""
                  : active
                    ? "is-active"
                    : "hover:bg-[var(--brand-soft)]"
              }`}
            >
              {batchMode ? (
                <label className="flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={batchDeleting}
                    onChange={() => toggleSelected(report.id)}
                    className="h-5 w-5 rounded border-slate-300 text-rose-600 focus:ring-rose-300"
                    aria-label={`选择日报 ${report.title}`}
                  />
                </label>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  if (batchMode) {
                    toggleSelected(report.id);
                    return;
                  }
                  onSelect(report);
                }}
                disabled={batchDeleting}
                aria-current={!batchMode && active ? "true" : undefined}
                aria-pressed={!batchMode ? active : undefined}
                className="min-h-11 min-w-0 flex-1 rounded-xl px-3 py-2 text-left disabled:opacity-60"
              >
                <div className="mb-2 flex items-center justify-between gap-3">
                  <span className="line-clamp-1 text-sm font-black text-slate-950">{report.title}</span>
                  <StatusPill tone={report.risk.level === "high" ? "red" : "amber"}>
                    {report.risk.level}
                  </StatusPill>
                </div>
                <div className="text-xs leading-5 text-slate-500">
                  {new Date(report.created_at).toLocaleString("zh-CN")}
                </div>
              </button>
              {!batchMode ? (
                <button
                  type="button"
                  disabled={deletingId === report.id}
                  aria-label={`删除日报 ${report.title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    setDeleteIntent({ kind: "single", reports: [report] });
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
      {hasMore ? (
        <button
          type="button"
          onClick={() => setVisibleCount((count) => Math.min(count + initialLimit, filteredReports.length))}
          className="btn-ghost mt-3 min-h-11 w-full text-xs"
        >
          再显示 {Math.min(initialLimit, filteredReports.length - visibleCount)} 份日报
        </button>
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
            aria-labelledby="history-delete-title"
            aria-describedby="history-delete-description"
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
                <h2 id="history-delete-title" className="text-base font-black text-slate-950">
                  {deleteIntent.kind === "batch"
                    ? `删除选中的 ${deleteCount} 份日报？`
                    : "删除这份日报？"}
                </h2>
                <p id="history-delete-description" className="mt-1 text-sm leading-6 text-slate-600">
                  删除后无法恢复，请确认以下日报不再需要。
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
                  以及另外 {deleteCount - deletePreview.length} 份日报
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
