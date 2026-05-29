"use client";

import { History, RefreshCw } from "lucide-react";
import type { Report } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

type HistoryRailProps = {
  reports: Report[];
  onRefresh: () => void;
  onSelect: (report: Report) => void;
};

export function HistoryRail({ reports, onRefresh, onSelect }: HistoryRailProps) {
  return (
    <aside className="glass-panel min-w-0 rounded-[28px] p-5">
      <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-2 font-black text-slate-950">
          <History size={18} />
          历史日报
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-blue-600"
          aria-label="刷新历史日报"
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <div className="space-y-3">
        {reports.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-500">
            生成第一份日报后会自动保存到这里。
          </div>
        ) : null}
        {reports.map((report) => (
          <button
            type="button"
            key={report.id}
            onClick={() => onSelect(report)}
            className="block w-full rounded-2xl bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
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
        ))}
      </div>
    </aside>
  );
}
