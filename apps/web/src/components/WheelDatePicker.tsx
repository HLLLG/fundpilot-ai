"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";

const ITEM_HEIGHT = 44;
const PADDING_COUNT = 2;

type WheelColumnProps = {
  items: Array<{ value: number; label: string }>;
  value: number;
  onChange: (value: number) => void;
};

function WheelColumn({ items, value, onChange }: WheelColumnProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const scrollEndTimer = useRef<number | null>(null);
  const syncingRef = useRef(false);

  const selectedIndex = Math.max(
    0,
    items.findIndex((item) => item.value === value),
  );

  const scrollToIndex = useCallback((index: number, smooth = false) => {
    const scroller = scrollerRef.current;
    if (!scroller) {
      return;
    }
    syncingRef.current = true;
    scroller.scrollTo({ top: index * ITEM_HEIGHT, behavior: smooth ? "smooth" : "auto" });
    window.setTimeout(() => {
      syncingRef.current = false;
    }, smooth ? 180 : 0);
  }, []);

  useEffect(() => {
    if (items.length === 0) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      scrollToIndex(selectedIndex);
    });
    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [items, selectedIndex, scrollToIndex]);

  const settleSelection = useCallback(() => {
    const scroller = scrollerRef.current;
    if (!scroller || items.length === 0) {
      return;
    }
    const index = Math.max(0, Math.min(items.length - 1, Math.round(scroller.scrollTop / ITEM_HEIGHT)));
    scrollToIndex(index, true);
    const nextValue = items[index]?.value;
    if (nextValue != null && nextValue !== value) {
      onChange(nextValue);
    }
  }, [items, onChange, scrollToIndex, value]);

  const handleScroll = () => {
    if (syncingRef.current) {
      return;
    }
    if (scrollEndTimer.current != null) {
      window.clearTimeout(scrollEndTimer.current);
    }
    scrollEndTimer.current = window.setTimeout(() => {
      settleSelection();
    }, 90);
  };

  return (
    <div className="relative h-[220px] flex-1 overflow-hidden">
      <div
        ref={scrollerRef}
        onScroll={handleScroll}
        onMouseUp={settleSelection}
        onTouchEnd={settleSelection}
        className="h-full overflow-y-auto scroll-smooth [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
        style={{ scrollSnapType: "y mandatory" }}
      >
        {Array.from({ length: PADDING_COUNT }, (_, index) => (
          <div key={`top-${index}`} style={{ height: ITEM_HEIGHT }} aria-hidden />
        ))}
        {items.map((item) => (
          <div
            key={item.value}
            style={{ height: ITEM_HEIGHT, scrollSnapAlign: "center" }}
            className="flex items-center justify-center text-[17px] font-semibold tabular-nums text-slate-800"
          >
            {item.label}
          </div>
        ))}
        {Array.from({ length: PADDING_COUNT }, (_, index) => (
          <div key={`bottom-${index}`} style={{ height: ITEM_HEIGHT }} aria-hidden />
        ))}
      </div>
    </div>
  );
}

function daysInMonth(year: number, month: number) {
  return new Date(year, month, 0).getDate();
}

function parseIsoDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return null;
  }
  return { year, month, day };
}

function toIsoDate(year: number, month: number, day: number) {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export type WheelDatePickerProps = {
  value: string;
  max?: string;
  minYear?: number;
  onChange: (value: string) => void;
};

export function WheelDatePicker({
  value,
  max,
  minYear = 1990,
  onChange,
}: WheelDatePickerProps) {
  const maxParts = useMemo(() => parseIsoDate(max ?? todayIsoDate()), [max]);
  const parsed = useMemo(() => parseIsoDate(value), [value]);

  const year = parsed?.year ?? maxParts?.year ?? new Date().getFullYear();
  const month = parsed?.month ?? maxParts?.month ?? new Date().getMonth() + 1;
  const day = parsed?.day ?? maxParts?.day ?? new Date().getDate();

  const maxYear = maxParts?.year ?? new Date().getFullYear();
  const years = useMemo(
    () =>
      Array.from({ length: maxYear - minYear + 1 }, (_, index) => {
        const nextYear = minYear + index;
        return { value: nextYear, label: `${nextYear}年` };
      }),
    [maxYear, minYear],
  );

  const months = useMemo(() => {
    const upperMonth = year === maxParts?.year ? (maxParts?.month ?? 12) : 12;
    return Array.from({ length: upperMonth }, (_, index) => {
      const nextMonth = index + 1;
      return { value: nextMonth, label: `${nextMonth}月` };
    });
  }, [maxParts?.month, maxParts?.year, year]);

  const days = useMemo(() => {
    const monthDays = daysInMonth(year, month);
    let upperDay = monthDays;
    if (year === maxParts?.year && month === maxParts?.month) {
      upperDay = Math.min(upperDay, maxParts?.day ?? upperDay);
    }
    return Array.from({ length: upperDay }, (_, index) => {
      const nextDay = index + 1;
      return { value: nextDay, label: `${nextDay}日` };
    });
  }, [maxParts?.day, maxParts?.month, maxParts?.year, month, year]);

  useEffect(() => {
    const validMonth = months.some((item) => item.value === month)
      ? month
      : (months[months.length - 1]?.value ?? month);
    const validDay = days.some((item) => item.value === day)
      ? day
      : (days[days.length - 1]?.value ?? day);
    const nextValue = toIsoDate(year, validMonth, validDay);
    if (nextValue !== value) {
      onChange(nextValue);
    }
  }, [day, days, month, months, onChange, value, year]);

  const emitChange = (nextYear: number, nextMonth: number, nextDay: number) => {
    const clampedMonth =
      nextYear === maxParts?.year ? Math.min(nextMonth, maxParts?.month ?? nextMonth) : nextMonth;
    const maxDay = daysInMonth(nextYear, clampedMonth);
    let clampedDay = Math.min(nextDay, maxDay);
    if (
      nextYear === maxParts?.year &&
      clampedMonth === maxParts?.month &&
      maxParts?.day != null
    ) {
      clampedDay = Math.min(clampedDay, maxParts.day);
    }
    onChange(toIsoDate(nextYear, clampedMonth, clampedDay));
  };

  return (
    <div className="relative overflow-hidden rounded-2xl bg-slate-50">
      <div className="pointer-events-none absolute inset-x-3 top-1/2 z-10 h-11 -translate-y-1/2 rounded-xl border border-slate-200/80 bg-white/70 shadow-sm" />
      <div
        className="pointer-events-none absolute inset-x-0 top-0 z-20 h-16 bg-gradient-to-b from-slate-50 via-slate-50/80 to-transparent"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute inset-x-0 bottom-0 z-20 h-16 bg-gradient-to-t from-slate-50 via-slate-50/80 to-transparent"
        aria-hidden
      />
      <div className="relative z-0 flex px-1">
        <WheelColumn
          items={years}
          value={year}
          onChange={(nextYear) => emitChange(nextYear, month, day)}
        />
        <WheelColumn
          items={months}
          value={month}
          onChange={(nextMonth) => emitChange(year, nextMonth, day)}
        />
        <WheelColumn
          items={days}
          value={Math.min(day, days[days.length - 1]?.value ?? day)}
          onChange={(nextDay) => emitChange(year, month, nextDay)}
        />
      </div>
    </div>
  );
}

export function todayIsoDate() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export function inferPurchaseDateFromHoldingDays(holdingDays: number) {
  const today = parseIsoDate(todayIsoDate());
  if (!today) {
    return todayIsoDate();
  }
  const anchor = new Date(today.year, today.month - 1, today.day);
  anchor.setDate(anchor.getDate() - Math.max(0, holdingDays));
  return toIsoDate(anchor.getFullYear(), anchor.getMonth() + 1, anchor.getDate());
}

export function resolveInitialPurchaseDate(
  holdingDays: number | null,
  firstPurchaseDate: string,
  holdingDaysSource?: string,
) {
  if (holdingDaysSource === "user" && firstPurchaseDate) {
    return firstPurchaseDate;
  }
  if (holdingDays != null && holdingDays >= 0) {
    return inferPurchaseDateFromHoldingDays(holdingDays);
  }
  if (firstPurchaseDate) {
    return firstPurchaseDate;
  }
  const fallback = new Date();
  fallback.setFullYear(fallback.getFullYear() - 1);
  return toIsoDate(fallback.getFullYear(), fallback.getMonth() + 1, fallback.getDate());
}
