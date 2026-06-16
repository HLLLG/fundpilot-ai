"use client";

import { useMemo, useState } from "react";
import { History, RefreshCw, Trash2, X } from "lucide-react";
import type { FundDiscoveryReport } from "@/lib/api";
import { deleteDiscoveryReport } from "@/lib/api";

type DiscoveryHistoryRailProps = {
  reports: FundDiscoveryReport[];
  activeReportId?: string | null;
  onRefresh: () => void;
  onSelect: (report: FundDiscoveryReport) => void;
  onDeleted?: (reportId: string) => void;
};

export function DiscoveryHistoryRail({
  reports,
  activeReportId,
  onRefresh,
  onSelect,
  onDeleted,
}: DiscoveryHistoryRailProps) {
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
      selectedCount > titles.length ? `\n…等共 ${selectedCount} 份推荐报告` : "";
    if (!window.confirm(`确定删除选中的 ${selectedCount} 份推荐报告吗？\n\n${preview}${suffix}`)) {
      return;
    }

    setBatchDeleting(true);
    try {
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
          历史推荐
        </div>
        <div className="flex items-center gap-2">
          {batchMode ? (
            <>
              <button
                type="button"
                onClick={toggleSelectAll}
                disabled={batchDeleting || reports.length === 0}
                className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-600 transition hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-50"
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
                className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-indigo-600"
                aria-label="刷新历史推荐"
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
            完成首次扫描后会自动保存到这里。
          </div>
        ) : null}
        {reports.map((item) => {
          const active = item.id === activeReportId;
          const selected = selectedIds.has(item.id);
          return (
            <div
              key={item.id}
              className={`flex items-stretch gap-2 rounded-2xl p-2 shadow-sm transition ${
                batchMode
                  ? selected
                    ? "ring-2 ring-rose-200"
                    : "bg-white"
                  : active
                    ? "bg-indigo-50 ring-1 ring-indigo-200"
                    : "bg-white hover:-translate-y-0.5 hover:shadow-md"
              }`}
            >
              {batchMode ? (
                <label className="flex shrink-0 cursor-pointer items-center px-2">
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
                onClick={() => {
                  if (batchMode) {
                    toggleSelected(item.id);
                    return;
                  }
                  onSelect(item);
                }}
                disabled={batchDeleting}
                className="min-w-0 flex-1 rounded-xl px-3 py-2 text-left disabled:opacity-60"
              >
                <div className="line-clamp-2 text-sm font-black text-slate-950">{item.title}</div>
                <div className="mt-1 text-xs text-slate-500">
                  {new Date(item.created_at).toLocaleString("zh-CN")}
                </div>
                {item.target_sectors?.length ? (
                  <div className="mt-1 line-clamp-1 text-[11px] text-slate-400">
                    {item.target_sectors.join("、")}
                  </div>
                ) : null}
              </button>
              {!batchMode ? (
                <button
                  type="button"
                  disabled={deletingId === item.id}
                  aria-label={`删除推荐报告 ${item.title}`}
                  onClick={async (event) => {
                    event.stopPropagation();
                    if (!window.confirm(`确定删除这份推荐报告吗？\n${item.title}`)) {
                      return;
                    }
                    setDeletingId(item.id);
                    try {
                      await deleteDiscoveryReport(item.id);
                      onDeleted?.(item.id);
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
