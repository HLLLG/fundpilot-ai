// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ProfitLossCalendar, profitCalendarCellLabel } from "@/components/ProfitLossCalendar";
import type { ProfitCalendar } from "@/lib/api";

afterEach(cleanup);

const calendar: ProfitCalendar = {
  year: 2026,
  month: 7,
  days: [
    {
      date: "2026-07-01",
      day: 1,
      weekday: 3,
      is_trading_day: true,
      is_today: true,
      is_holiday: false,
      daily_profit: 12.34,
      daily_return_percent: 0.56,
    },
    {
      date: "2026-07-02",
      day: 2,
      weekday: 4,
      is_trading_day: false,
      is_today: false,
      is_holiday: true,
      daily_profit: null,
      daily_return_percent: null,
    },
    {
      date: "2026-07-03",
      day: 3,
      weekday: 5,
      is_trading_day: true,
      is_today: false,
      is_holiday: false,
      is_pending_update: true,
      daily_profit: null,
      daily_return_percent: null,
    },
  ],
  month_cumulative_profit: 12.34,
  month_index_return_percent: -0.2,
};

describe("ProfitLossCalendar", () => {
  it("provides a semantic table and truthful labels for trading, closed, and pending days", () => {
    render(
      <ProfitLossCalendar
        calendar={calendar}
        showReturnPercent={false}
        onToggleMode={vi.fn()}
        onMonthChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("table", { name: "2026年7月每日收益额" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "2026年7月1日，今天，收益额+12.34元" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "2026年7月2日，休市" })).toHaveTextContent("休市");
    expect(screen.getByRole("cell", { name: "2026年7月3日，收益额未更新" })).toHaveTextContent("未更新");
  });

  it("labels 44px navigation controls and reports the shifted month", () => {
    const onMonthChange = vi.fn();
    render(
      <ProfitLossCalendar
        calendar={calendar}
        showReturnPercent
        onToggleMode={vi.fn()}
        onMonthChange={onMonthChange}
      />,
    );

    const previous = screen.getByRole("button", { name: "上个月" });
    const next = screen.getByRole("button", { name: "下个月" });
    expect(previous).toHaveClass("touch-target");
    expect(next).toHaveClass("touch-target");
    fireEvent.click(previous);
    fireEvent.click(next);
    expect(onMonthChange).toHaveBeenNthCalledWith(1, 2026, 6);
    expect(onMonthChange).toHaveBeenNthCalledWith(2, 2026, 8);
  });

  it("distinguishes a real zero from missing return data in its accessible summary", () => {
    const zeroDay = { ...calendar.days[0], daily_return_percent: 0 };
    const missingDay = { ...calendar.days[0], daily_return_percent: null };

    expect(profitCalendarCellLabel(zeroDay, true)).toContain("收益率0.00%");
    expect(profitCalendarCellLabel(missingDay, true)).toContain("收益率暂无数据");
  });
});
