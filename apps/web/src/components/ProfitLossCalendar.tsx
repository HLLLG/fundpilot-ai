"use client";

import { ChevronLeft, ChevronRight, ArrowLeftRight } from "lucide-react";
import type { ProfitCalendar } from "@/lib/api";

const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

type ProfitLossCalendarProps = {
  calendar: ProfitCalendar | null | undefined;
  showReturnPercent: boolean;
  onToggleMode: () => void;
  onMonthChange: (year: number, month: number) => void;
};

function formatMoney(value: number | null | undefined) {
  if (value == null) {
    return "";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}`;
}

function formatReturn(value: number | null | undefined) {
  if (value == null) {
    return "";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

function cellTone(value: number | null | undefined) {
  if (value == null || value === 0) {
    return "bg-slate-50 text-slate-400";
  }
  return value > 0 ? "bg-rose-50 text-rose-600" : "bg-emerald-50 text-emerald-600";
}

function profitTextClass(value: number | null | undefined) {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "text-rose-600" : "text-emerald-600";
}

export function ProfitLossCalendar({
  calendar,
  showReturnPercent,
  onToggleMode,
  onMonthChange,
}: ProfitLossCalendarProps) {
  if (!calendar) {
    return null;
  }

  const { year, month } = calendar;
  const firstWeekday = new Date(year, month - 1, 1).getDay();
  const offset = firstWeekday === 0 ? 6 : firstWeekday - 1;
  const cells: Array<ProfitCalendar["days"][number] | null> = [
    ...Array.from({ length: offset }, () => null),
    ...calendar.days,
  ];

  function shiftMonth(delta: number) {
    const date = new Date(year, month - 1 + delta, 1);
    onMonthChange(date.getFullYear(), date.getMonth() + 1);
  }

  return (
    <section className="pl-panel">
      <div className="pl-panel-head">
        <div className="pl-panel-title">盈亏日历</div>
        <button
          type="button"
          onClick={onToggleMode}
          className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-500 hover:text-slate-800"
        >
          <ArrowLeftRight size={12} />
          {showReturnPercent ? "收益额" : "收益率"}
        </button>
      </div>

      <div className="mb-3 flex items-center justify-between">
        <button
          type="button"
          onClick={() => shiftMonth(-1)}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          <ChevronLeft size={18} />
        </button>
        <div className="text-sm font-extrabold tabular-nums text-slate-900">
          {year}年{month}月
        </div>
        <button
          type="button"
          onClick={() => shiftMonth(1)}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          <ChevronRight size={18} />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1 text-center text-[11px] font-bold text-slate-400">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="py-1">
            {label}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-1">
        {cells.map((day, index) => {
          if (!day) {
            return <div key={`empty-${index}`} />;
          }
          const value = showReturnPercent ? day.daily_return_percent : day.daily_profit;
          const display = day.is_holiday
            ? "休"
            : showReturnPercent
              ? formatReturn(value)
              : formatMoney(value);
          const tone = day.is_holiday
            ? "bg-slate-100 text-slate-400"
            : day.is_today
              ? "bg-[var(--brand)] text-white ring-2 ring-[var(--brand-soft)]"
              : cellTone(typeof value === "number" ? value : null);

          return (
            <div
              key={day.date}
              className={`min-h-[52px] rounded-lg px-0.5 py-1 text-center ${tone}`}
            >
              <div className="text-[10px] font-bold opacity-80">{day.is_today ? "今" : day.day}</div>
              {display && display !== "休" ? (
                <div className="mt-0.5 text-[9px] font-bold leading-tight">{display}</div>
              ) : day.is_holiday ? (
                <div className="mt-0.5 text-[9px] font-bold">休</div>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="pl-chart-footer !mt-3 !border-t !pt-3">
        <span>
          {calendar.month}月累计收益：
          <span className={profitTextClass(calendar.month_cumulative_profit)}>
            {formatMoney(calendar.month_cumulative_profit)}
          </span>
        </span>
        <span>
          上证指数：
          <span className={profitTextClass(calendar.month_index_return_percent)}>
            {formatReturn(calendar.month_index_return_percent)}
          </span>
        </span>
      </div>
    </section>
  );
}
