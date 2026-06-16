"use client";

import { useMemo, useState } from "react";
import { History, RefreshCw, Trash2, X } from "lucide-react";
import type { Report } from "@/lib/api";
import { deleteReport } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

type HistoryRailProps = {
  reports: Report[];
  onRefresh: () => void;
  onSelect: (report: Report) => void;
  onDeleted?: (reportId: string) => void;
};

export function HistoryRail({ reports, onRefresh, onSelect, onDeleted }: HistoryRailProps) {
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);

  const selectedCount = selectedIds.size;
  const allSelected = reports.length > 0 && selectedCount === reports.length;

  const selectedIdList = useMemo(() => [...selectedIds], [selectedIds]);

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

  const handleBatchDelete = async () => {
    if (selectedCount === 0) {
      return;
    }
    const titles = reports
      .filter((report) => selectedIds.has(report.id))
      .map((report) => report.title)
      .slice(0, 3);
    const preview = titles.join("\n");
    const suffix =
      selectedCount > titles.length ? `\n…等共 ${selectedCount} 份日报` : "";
    if (!window.confirm(`确定删除选中的 ${selectedCount} 份日报吗？\n\n${preview}${suffix}`)) {
      return;
    }

    setBatchDeleting(true);
    try {
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
        window.alert(`${failed} 份删除失败，其余已删除。`);
      }
    } catch {
      window.alert("批量删除失败，请稍后重试。");
    } finally {
      setBatchDeleting(false);
    }
  };

  return (
    <aside className="glass-panel min-w-0 rounded-[28px] p-5">
      <div className="mb-5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-black text-slate-950">
          <History size={18} />
          历史日报
        </div>
        <div className="flex items-center gap-2">
          {batchMode ? (
            <>
              <button
                type="button"
                onClick={toggleSelectAll}
                disabled={batchDeleting || reports.length === 0}
                className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-600 transition hover:border-blue-300 hover:text-blue-700 disabled:opacity-50"
              >
                {allSelected ? "取消全选" : "全选"}
              </button>
              <button
                type="button"
                onClick={() => void handleBatchDelete()}
                disabled={batchDeleting || selectedCount === 0}
                className="inline-flex items-center gap-1 rounded-full bg-rose-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-rose-700 disabled:opacity-50"
              >
                <Trash2 size={12} />
                {batchDeleting ? "删除中…" : `删除(${selectedCount})`}
              </button>
              <button
                type="button"
                onClick={exitBatchMode}
                disabled={batchDeleting}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-slate-800 disabled:opacity-50"
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
                  className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-600 transition hover:border-rose-300 hover:text-rose-700"
                >
                  批量删除
                </button>
              ) : null}
              <button
                type="button"
                onClick={onRefresh}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-blue-600"
                aria-label="刷新历史日报"
              >
                <RefreshCw size={16} />
              </button>
            </>
          )}
        </div>
      </div>
      <div className="space-y-3">
        {reports.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-500">
            生成第一份日报后会自动保存到这里。
          </div>
        ) : null}
        {reports.map((report) => {
          const selected = selectedIds.has(report.id);
          return (
            <div
              key={report.id}
              className={`flex items-stretch gap-2 rounded-2xl bg-white p-2 shadow-sm transition ${
                batchMode
                  ? selected
                    ? "ring-2 ring-rose-200"
                    : ""
                  : "hover:-translate-y-0.5 hover:shadow-md"
              }`}
            >
              {batchMode ? (
                <label className="flex shrink-0 cursor-pointer items-center px-2">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={batchDeleting}
                    onChange={() => toggleSelected(report.id)}
                    className="h-4 w-4 rounded border-slate-300 text-rose-600 focus:ring-rose-300"
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
                className="min-w-0 flex-1 rounded-xl px-3 py-2 text-left disabled:opacity-60"
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
                  onClick={async (event) => {
                    event.stopPropagation();
                    if (!window.confirm(`确定删除这份日报吗？\n${report.title}`)) {
                      return;
                    }
                    setDeletingId(report.id);
                    try {
                      await deleteReport(report.id);
                      onDeleted?.(report.id);
                      await onRefresh();
                    } catch {
                      window.alert("删除失败，请稍后重试。");
                    } finally {
                      setDeletingId(null);
                    }
                  }}
                  className="inline-flex w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                >
                  <Trash2 size={16} />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
