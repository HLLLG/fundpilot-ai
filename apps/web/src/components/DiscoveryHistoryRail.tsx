"use client";

import { useState } from "react";
import { History, RefreshCw, Trash2 } from "lucide-react";
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

  return (
    <aside className="glass-panel min-w-0 rounded-[28px] p-5">
      <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-2 font-black text-slate-950">
          <History size={18} />
          历史推荐
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-indigo-600"
          aria-label="刷新历史推荐"
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <div className="space-y-3">
        {reports.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-500">
            完成首次扫描后会自动保存到这里。
          </div>
        ) : null}
        {reports.map((item) => {
          const active = item.id === activeReportId;
          return (
            <div
              key={item.id}
              className={`flex items-stretch gap-2 rounded-2xl p-2 shadow-sm transition ${
                active ? "bg-indigo-50 ring-1 ring-indigo-200" : "bg-white hover:-translate-y-0.5 hover:shadow-md"
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(item)}
                className="min-w-0 flex-1 rounded-xl px-3 py-2 text-left"
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
            </div>
          );
        })}
      </div>
    </aside>
  );
}
