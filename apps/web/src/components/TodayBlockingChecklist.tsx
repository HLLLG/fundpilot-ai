"use client";

import { AlertCircle, AlertTriangle, Info } from "lucide-react";
import type { WorkflowBlocker } from "@/lib/workflowBlockers";

type TodayBlockingChecklistProps = {
  blockers: WorkflowBlocker[];
};

const iconBySeverity = {
  error: <AlertCircle size={16} className="text-rose-600" />,
  warn: <AlertTriangle size={16} className="text-amber-600" />,
  info: <Info size={16} className="text-blue-600" />,
};

const rowClassBySeverity = {
  error: "border-rose-100 bg-rose-50/80",
  warn: "border-amber-100 bg-amber-50/70",
  info: "border-blue-100 bg-blue-50/60",
};

export function TodayBlockingChecklist({ blockers }: TodayBlockingChecklistProps) {
  if (!blockers.length) {
    return null;
  }

  return (
    <div className="glass-panel rounded-[24px] p-4">
      <div className="mb-3 text-sm font-black text-slate-950">今日检查清单</div>
      <ul className="space-y-2">
        {blockers.map((item) => (
          <li
            key={item.id}
            className={`flex items-start gap-2 rounded-2xl border px-3 py-2.5 text-sm leading-6 text-slate-700 ${rowClassBySeverity[item.severity]}`}
          >
            <span className="mt-0.5 shrink-0">{iconBySeverity[item.severity]}</span>
            <span>{item.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
