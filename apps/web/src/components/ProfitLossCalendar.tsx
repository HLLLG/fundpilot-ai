"use client";

import { ChevronLeft, ChevronRight, ArrowLeftRight } from "lucide-react";
import type { ProfitCalendar, ProfitCalendarDay } from "@/lib/api";

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
    return "bg-slate-50 text-slate-500";
  }
  return value > 0 ? "bg-rose-50 profit-up" : "bg-emerald-50 profit-down";
}

function profitTextClass(value: number | null | undefined) {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

export function profitCalendarCellLabel(
  day: ProfitCalendarDay,
  showReturnPercent: boolean,
): string {
  const metricLabel = showReturnPercent ? "收益率" : "收益额";
  const value = showReturnPercent ? day.daily_return_percent : day.daily_profit;
  const dateLabel = `${day.date.slice(0, 4)}年${Number(day.date.slice(5, 7))}月${Number(day.date.slice(8, 10))}日`;
  if (day.is_pending_update) {
    return `${dateLabel}${day.is_today ? "，今天" : ""}，${metricLabel}未更新`;
  }
  if (!day.is_trading_day) {
    return `${dateLabel}${day.is_today ? "，今天" : ""}，休市`;
  }
  if (value == null) {
    return `${dateLabel}${day.is_today ? "，今天" : ""}，${metricLabel}暂无数据`;
  }
  return `${dateLabel}${day.is_today ? "，今天" : ""}，${metricLabel}${
    showReturnPercent ? formatReturn(value) : `${formatMoney(value)}元`
  }`;
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
  while (cells.length % 7 !== 0) {
    cells.push(null);
  }
  const weeks = Array.from({ length: cells.length / 7 }, (_, index) =>
    cells.slice(index * 7, index * 7 + 7),
  );

  function shiftMonth(delta: number) {
    const date = new Date(year, month - 1 + delta, 1);
    onMonthChange(date.getFullYear(), date.getMonth() + 1);
  }

  return (
    <section className="pl-panel">
      <div className="pl-panel-head">
        <h2 className="pl-panel-title">盈亏日历</h2>
        <button
          type="button"
          onClick={onToggleMode}
          className="touch-target inline-flex items-center gap-1 rounded-lg px-2 text-xs font-bold text-slate-600 hover:bg-slate-100 hover:text-slate-900"
          aria-label={`切换为${showReturnPercent ? "收益额" : "收益率"}`}
        >
          <ArrowLeftRight size={12} />
          {showReturnPercent ? "收益额" : "收益率"}
        </button>
      </div>

      <div className="mb-3 flex items-center justify-between">
        <button
          type="button"
          onClick={() => shiftMonth(-1)}
          className="touch-target flex items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800"
          aria-label="上个月"
        >
          <ChevronLeft size={18} />
        </button>
        <div className="text-sm font-extrabold tabular-nums text-slate-900">
          {year}年{month}月
        </div>
        <button
          type="button"
          onClick={() => shiftMonth(1)}
          className="touch-target flex items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800"
          aria-label="下个月"
        >
          <ChevronRight size={18} />
        </button>
      </div>

      <table className="w-full table-fixed border-separate border-spacing-1 text-center">
        <caption className="sr-only">
          {year}年{month}月{showReturnPercent ? "每日收益率" : "每日收益额"}
        </caption>
        <thead>
          <tr className="text-xs font-bold text-slate-500">
            {WEEKDAY_LABELS.map((label) => (
              <th key={label} scope="col" className="py-1 font-bold">
                周{label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {weeks.map((week, weekIndex) => (
            <tr key={`week-${weekIndex}`}>
              {week.map((day, dayIndex) => {
                if (!day) {
                  return <td key={`empty-${weekIndex}-${dayIndex}`} aria-hidden="true" />;
                }
                const value = showReturnPercent ? day.daily_return_percent : day.daily_profit;
                const isClosedDay = !day.is_trading_day;
                const isPending = Boolean(day.is_pending_update);
                const display = isPending
                  ? "未更新"
                  : isClosedDay
                    ? "休市"
                    : showReturnPercent
                      ? formatReturn(value)
                      : formatMoney(value);
                const tone = isPending
                  ? day.is_today
                    ? "bg-[var(--brand)] text-white ring-2 ring-[var(--brand-soft)]"
                    : "bg-slate-50 text-slate-500"
                  : isClosedDay
                    ? "bg-slate-50 text-slate-500"
                    : day.is_today
                      ? "bg-[var(--brand)] text-white ring-2 ring-[var(--brand-soft)]"
                      : cellTone(typeof value === "number" ? value : null);

                return (
                  <td key={day.date} aria-label={profitCalendarCellLabel(day, showReturnPercent)}>
                    <div className={`min-h-14 rounded-lg px-0.5 py-1.5 text-center ${tone}`}>
                      <div className="text-xs font-bold opacity-80">{day.is_today ? "今" : day.day}</div>
                      {display ? (
                        <div className="mt-1 text-[11px] font-bold leading-tight">
                          {display}
                        </div>
                      ) : null}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

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
