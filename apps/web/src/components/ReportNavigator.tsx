"use client";

import { CalendarDays, ChevronLeft, ChevronRight, History, RotateCcw } from "lucide-react";

import type { Report } from "@/lib/api";

type ReportNavigatorProps = {
  currentReport: Report | null;
  reportCount: number;
  currentLabel: string;
  currentStatus: string;
  hasPrevious: boolean;
  hasNext: boolean;
  canReturnToday: boolean;
  historyLoading?: boolean;
  historyError?: string | null;
  onPrevious: () => void;
  onNext: () => void;
  onToday: () => void;
  onOpenHistory: () => void;
};

export function ReportNavigator({
  currentReport,
  reportCount,
  currentLabel,
  currentStatus,
  hasPrevious,
  hasNext,
  canReturnToday,
  historyLoading = false,
  historyError,
  onPrevious,
  onNext,
  onToday,
  onOpenHistory,
}: ReportNavigatorProps) {
  return (
    <section className="report-navigator" aria-label="日报导航器">
      <div className="report-navigator-current" aria-live="polite">
        <span className="report-navigator-icon" aria-hidden="true">
          <CalendarDays size={18} />
        </span>
        <div className="min-w-0">
          <p className="section-eyebrow">{currentLabel}</p>
          <p className="truncate text-sm font-extrabold text-slate-950">
            {currentReport?.title ?? "尚未生成今日日报"}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {historyLoading ? "正在同步日报索引…" : historyError ?? currentStatus}
          </p>
        </div>
      </div>

      <div className="report-navigator-actions">
        <button
          type="button"
          onClick={onPrevious}
          disabled={!hasPrevious}
          className="report-nav-step min-h-11"
          aria-label="上一份日报"
        >
          <ChevronLeft size={17} />
          <span>上一份</span>
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!hasNext}
          className="report-nav-step min-h-11"
          aria-label="下一份日报"
        >
          <span>下一份</span>
          <ChevronRight size={17} />
        </button>
        <button
          type="button"
          onClick={onToday}
          disabled={!canReturnToday}
          className="report-nav-today min-h-11"
        >
          <RotateCcw size={15} />
          回到今日
        </button>
        <button
          type="button"
          onClick={onOpenHistory}
          className="report-nav-history min-h-11"
          aria-haspopup="dialog"
        >
          <History size={16} />
          全部历史
          {reportCount > 0 ? <strong>{reportCount}</strong> : null}
        </button>
      </div>
    </section>
  );
}
