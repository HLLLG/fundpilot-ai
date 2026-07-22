"use client";

import { ChevronRight, History, RefreshCw, Trash2, X } from "lucide-react";
import type { FundDiscoveryReport } from "@/lib/api";
import { deleteDiscoveryReport } from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";
import {
  HistoryDeleteDialog,
  type HistoryDeleteDialogCopy,
} from "@/components/HistoryDeleteDialog";
import { useHistoryRailController } from "@/lib/useHistoryRailController";

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

function discoveryReportSearchText(report: FundDiscoveryReport): string {
  return [
    report.title,
    ...(report.target_sectors ?? []),
    new Date(report.created_at).toLocaleDateString("zh-CN"),
  ].join(" ");
}

function deleteDiscoveryHistoryReport(reportId: string): Promise<void> {
  return deleteDiscoveryReport(reportId);
}

const DELETE_DIALOG_COPY = {
  singleTitle: "删除这份推荐报告？",
  batchTitle: (count: number) => `删除选中的 ${count} 份推荐报告？`,
  description: "删除后无法恢复，请确认以下推荐报告不再需要。",
  additionalItems: (count: number) => `以及另外 ${count} 份推荐报告`,
} satisfies HistoryDeleteDialogCopy;

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
  const history = useHistoryRailController({
    reports,
    activeReportId,
    initialLimit,
    getSearchText: discoveryReportSearchText,
    deleteItem: deleteDiscoveryHistoryReport,
    onRefresh,
    onDeleted,
  });

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
          {history.batchMode ? (
            <>
              <button
                type="button"
                onClick={history.toggleSelectAll}
                disabled={history.batchDeleting || reports.length === 0}
                className="min-h-11 rounded-full border border-slate-200 bg-white px-3 text-xs font-bold text-slate-600 transition hover:border-[rgba(37,99,235,0.4)] hover:text-[var(--brand-strong)] disabled:opacity-50"
              >
                {history.allSelected ? "取消全选" : "全选"}
              </button>
              <button
                type="button"
                onClick={history.requestBatchDelete}
                disabled={history.batchDeleting || history.selectedCount === 0}
                className="inline-flex min-h-11 items-center gap-1 rounded-full bg-[var(--danger-icon)] px-3 text-xs font-bold text-white transition hover:bg-[var(--danger-fg)] disabled:opacity-50"
              >
                <Trash2 size={12} />
                {history.batchDeleting ? "删除中…" : `删除(${history.selectedCount})`}
              </button>
              <button
                type="button"
                onClick={history.exitBatchMode}
                disabled={history.batchDeleting}
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
                  onClick={history.enterBatchMode}
                  className="min-h-11 rounded-full border border-slate-200 bg-white px-3 text-xs font-bold text-slate-600 transition hover:border-[var(--danger-border)] hover:text-[var(--danger-fg)]"
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
      {history.deleteFeedback ? (
        <InlineNotice
          tone={history.deleteFeedback.tone}
          message={history.deleteFeedback.message}
          className="mb-3"
        />
      ) : null}
      {variant === "drawer" && !history.batchMode && reports.length > 0 ? (
        <label className="history-search-field">
          <span className="sr-only">搜索历史推荐</span>
          <input
            type="search"
            value={history.query}
            onChange={(event) => history.setQuery(event.target.value)}
            placeholder="搜索标题、板块或日期"
            className="min-h-11"
          />
        </label>
      ) : null}
      <div
        className="history-scroll-region"
        data-testid="discovery-history-scroll-region"
        role="region"
        aria-label="历史推荐列表"
      >
        {reports.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-500">
            完成首次扫描后会自动保存到这里。
          </div>
        ) : null}
        {reports.length > 0 && history.filteredReports.length === 0 ? (
          <div className="history-empty-search">没有匹配的历史推荐，请换一个关键词。</div>
        ) : null}
        {history.visibleReports.map((item) => {
          const active = item.id === activeReportId;
          const selected = history.selectedIds.has(item.id);
          return (
            <div
              key={item.id}
              data-testid="discovery-history-item"
              className={`flex items-stretch gap-2 rounded-2xl p-2 shadow-sm transition ${
                history.batchMode
                  ? selected
                    ? "ring-2 ring-rose-200"
                    : "bg-white"
                  : active
                    ? "bg-[var(--brand-soft)] ring-1 ring-[rgba(37,99,235,0.25)]"
                    : "bg-white hover:-translate-y-0.5 hover:shadow-md"
              }`}
            >
              {history.batchMode ? (
                <label className="touch-target flex shrink-0 cursor-pointer items-center justify-center">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={history.batchDeleting}
                    onChange={() => history.toggleSelected(item.id)}
                    className="h-4 w-4 rounded border-slate-300 text-[var(--danger-icon)] focus:ring-rose-300"
                    aria-label={`选择推荐报告 ${item.title}`}
                  />
                </label>
              ) : null}
              <button
                type="button"
                aria-current={!history.batchMode && active ? "true" : undefined}
                aria-pressed={!history.batchMode ? active : undefined}
                onClick={() => {
                  if (history.batchMode) {
                    history.toggleSelected(item.id);
                    return;
                  }
                  onSelect(item);
                }}
                disabled={history.batchDeleting}
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
              {!history.batchMode ? (
                <button
                  type="button"
                  disabled={history.deletingId === item.id}
                  aria-label={`删除推荐报告 ${item.title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    history.requestSingleDelete(item);
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
      {!history.batchMode && reports.length > 0 ? (
        <div className="history-rail-footer">
          {history.hasMore ? (
            <button
              type="button"
              onClick={history.showMore}
              className="btn-ghost min-h-11 flex-1 text-xs"
            >
              再显示 {Math.min(initialLimit, history.filteredReports.length - history.visibleCount)} 份
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
