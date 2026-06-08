import type { FundNavPoint, IndexDailyPoint } from "@/lib/api";

export const PERFORMANCE_PERIODS = [
  { label: "近1月", days: 22 },
  { label: "近3月", days: 63 },
  { label: "近6月", days: 126 },
  { label: "近1年", days: 252 },
  { label: "近3年", days: 756 },
] as const;

export type PerformanceSeriesPoint = {
  date: string;
  nav: number;
  dailyReturn: number | null;
  fundPercent: number;
  benchPercent: number | null;
};

function normalizeDate(date: string) {
  return date.slice(0, 10);
}

function buildBenchLookup(points: IndexDailyPoint[]) {
  const sorted = [...points]
    .map((point) => ({ date: normalizeDate(point.date), close: point.close }))
    .sort((left, right) => left.date.localeCompare(right.date));
  const byDate = new Map(sorted.map((point) => [point.date, point.close]));
  return { sorted, byDate };
}

function benchCloseOnDate(
  date: string,
  lookup: ReturnType<typeof buildBenchLookup>,
): number | null {
  const exact = lookup.byDate.get(date);
  if (exact != null) {
    return exact;
  }
  let fallback: number | null = null;
  for (const point of lookup.sorted) {
    if (point.date > date) {
      break;
    }
    fallback = point.close;
  }
  return fallback;
}

export function buildPerformanceSeries(
  fundPoints: FundNavPoint[],
  benchPoints: IndexDailyPoint[],
): PerformanceSeriesPoint[] {
  if (fundPoints.length < 2) {
    return [];
  }

  const lookup = buildBenchLookup(benchPoints);
  const firstDate = normalizeDate(fundPoints[0].date);
  const fundBase = fundPoints[0].nav;
  let benchBase = benchCloseOnDate(firstDate, lookup);
  if (benchBase == null && lookup.sorted.length > 0) {
    benchBase = lookup.sorted[0].close;
  }

  return fundPoints.map((point, index) => {
    const date = normalizeDate(point.date);
    const benchClose = benchCloseOnDate(date, lookup);
    let dailyReturn = point.daily_return_percent ?? null;
    if (dailyReturn == null && index > 0) {
      const prevNav = fundPoints[index - 1].nav;
      if (prevNav > 0) {
        dailyReturn = Math.round((point.nav / prevNav - 1) * 10000) / 100;
      }
    }
    return {
      date,
      nav: point.nav,
      dailyReturn,
      fundPercent: fundBase > 0 ? Math.round((point.nav / fundBase - 1) * 10000) / 100 : 0,
      benchPercent:
        benchBase != null && benchBase > 0 && benchClose != null
          ? Math.round((benchClose / benchBase - 1) * 10000) / 100
          : null,
    };
  }).map((point, index, all) => {
    if (point.benchPercent != null || index === 0) {
      return point;
    }
    const previous = all[index - 1]?.benchPercent;
    return previous != null ? { ...point, benchPercent: previous } : point;
  });
}

export function formatSignedPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

export function cnSignedPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value) || Math.abs(value) < 0.005) {
    return "text-slate-500";
  }
  return value > 0 ? "text-rose-600" : "text-emerald-600";
}
