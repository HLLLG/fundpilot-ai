"use client";

import { History, RefreshCw, Trash2, X } from "lucide-react";
import type { Report } from "@/lib/api";
import { deleteReport } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";
import { InlineNotice } from "@/components/InlineNotice";
import {
  HistoryDeleteDialog,
  type HistoryDeleteDialogCopy,
} from "@/components/HistoryDeleteDialog";
import { useHistoryRailController } from "@/lib/useHistoryRailController";

type HistoryRailProps = {
  reports: Report[];
  activeReportId?: string | null;
  onRefresh: () => void;
  onSelect: (report: Report) => void;
  onDeleted?: (reportId: string) => void;
  initialLimit?: number;
};

function reportDateText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
}

function reportSearchText(report: Report): string {
  return [report.title, reportDateText(report.created_at), report.risk.level].join(" ");
}

const DELETE_DIALOG_COPY = {
  singleTitle: "删除这份日报？",
  batchTitle: (count: number) => `删除选中的 ${count} 份日报？`,
  description: "删除后无法恢复，请确认以下日报不再需要。",
  additionalItems: (count: number) => `以及另外 ${count} 份日报`,
} satisfies HistoryDeleteDialogCopy;

export function HistoryRail({
  reports,
  activeReportId,
  onRefresh,
  onSelect,
  onDeleted,
  initialLimit = 20,
}: HistoryRailProps) {
  const history = useHistoryRailController({
    reports,
    activeReportId,
    initialLimit,
    getSearchText: reportSearchText,
    deleteItem: deleteReport,
    onRefresh,
    onDeleted,
  });

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
          {history.batchMode ? (
            <>
              <button
                type="button"
                onClick={history.toggleSelectAll}
                disabled={history.batchDeleting || reports.length === 0}
                className="min-h-11 min-w-11 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 transition hover:border-blue-300 hover:text-[var(--info-fg)] disabled:opacity-50"
              >
                {history.allSelected ? "取消全选" : "全选"}
              </button>
              <button
                type="button"
                onClick={history.requestBatchDelete}
                disabled={history.batchDeleting || history.selectedCount === 0}
                className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-full bg-[var(--danger-icon)] px-3 py-2 text-xs font-bold text-white transition hover:bg-[var(--danger-fg)] disabled:opacity-50"
              >
                <Trash2 size={12} />
                {history.batchDeleting ? "删除中…" : `删除(${history.selectedCount})`}
              </button>
              <button
                type="button"
                onClick={history.exitBatchMode}
                disabled={history.batchDeleting}
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
                  onClick={history.enterBatchMode}
                  className="min-h-11 min-w-11 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 transition hover:border-[var(--danger-border)] hover:text-[var(--danger-fg)]"
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
      {history.deleteFeedback ? (
        <InlineNotice
          tone={history.deleteFeedback.tone}
          message={history.deleteFeedback.message}
          className="mb-3"
        />
      ) : null}
      {!history.batchMode && reports.length > 0 ? (
        <label className="history-search-field">
          <span className="sr-only">搜索历史日报</span>
          <input
            type="search"
            value={history.query}
            onChange={(event) => history.setQuery(event.target.value)}
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
        {reports.length > 0 && history.filteredReports.length === 0 ? (
          <div className="history-empty-search">没有匹配的历史日报，请换一个关键词。</div>
        ) : null}
        {history.visibleReports.map((report) => {
          const selected = history.selectedIds.has(report.id);
          const active = report.id === activeReportId;
          return (
            <div
              key={report.id}
              data-testid="report-history-item"
              className={`history-archive-row flex items-stretch gap-2 border-b border-[var(--line)] py-2 transition ${
                history.batchMode
                  ? selected
                    ? "ring-2 ring-rose-200"
                    : ""
                  : active
                    ? "is-active"
                    : "hover:bg-[var(--brand-soft)]"
              }`}
            >
              {history.batchMode ? (
                <label className="flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={history.batchDeleting}
                    onChange={() => history.toggleSelected(report.id)}
                    className="h-5 w-5 rounded border-slate-300 text-[var(--danger-icon)] focus:ring-rose-300"
                    aria-label={`选择日报 ${report.title}`}
                  />
                </label>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  if (history.batchMode) {
                    history.toggleSelected(report.id);
                    return;
                  }
                  onSelect(report);
                }}
                disabled={history.batchDeleting}
                aria-current={!history.batchMode && active ? "true" : undefined}
                aria-pressed={!history.batchMode ? active : undefined}
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
              {!history.batchMode ? (
                <button
                  type="button"
                  disabled={history.deletingId === report.id}
                  aria-label={`删除日报 ${report.title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    history.requestSingleDelete(report);
                  }}
                  className="inline-flex min-h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-500 transition hover:bg-[var(--danger-bg)] hover:text-[var(--danger-icon)] disabled:opacity-50"
                >
                  <Trash2 size={16} />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
      {history.hasMore ? (
        <button
          type="button"
          onClick={history.showMore}
          className="btn-ghost mt-3 min-h-11 w-full text-xs"
        >
          再显示 {Math.min(initialLimit, history.filteredReports.length - history.visibleCount)} 份日报
        </button>
      ) : null}
      {history.deleteIntent ? (
        <HistoryDeleteDialog
          intent={history.deleteIntent}
          copy={DELETE_DIALOG_COPY}
          onClose={history.closeDeleteDialog}
          onConfirm={history.confirmDelete}
        />
      ) : null}
    </aside>
  );
}
