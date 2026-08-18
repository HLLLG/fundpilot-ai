import type { FundNavPoint, FundTransaction } from "@/lib/api";

export type HoldingProfitPoint = {
  date: string;
  nav: number;
  shares: number;
  costBasis: number;
  marketValue: number;
  dailyProfit: number | null;
  cumulativeProfit: number;
  holdingReturnPercent: number | null;
};

export type HoldingProfitFallback = {
  shares?: number | null;
  unitCost?: number | null;
  firstHoldDate?: string | null;
  holdingDays?: number | null;
  currentProfit?: number | null;
  currentReturnPercent?: number | null;
};

const INACTIVE_STATUS = new Set(["skipped", "superseded", "reversed"]);

function round2(value: number) {
  return Math.round(value * 100) / 100;
}

function round4(value: number) {
  return Math.round(value * 10000) / 10000;
}

export function normalizeIsoDate(value: string | null | undefined) {
  const date = value?.slice(0, 10) ?? "";
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : null;
}

export function shiftIsoDate(iso: string, days: number) {
  const [year, month, day] = iso.split("-").map(Number);
  const next = new Date(year, month - 1, day + days);
  const yyyy = next.getFullYear();
  const mm = String(next.getMonth() + 1).padStart(2, "0");
  const dd = String(next.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

export function localTodayIso(now = new Date()) {
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

export function inferFirstHoldDate(
  firstPurchaseDate?: string | null,
  holdingDays?: number | null,
  today = localTodayIso(),
) {
  const explicit = normalizeIsoDate(firstPurchaseDate);
  const fromDays =
    holdingDays == null || holdingDays < 0 || !Number.isFinite(holdingDays)
      ? null
      : shiftIsoDate(today, -Math.floor(holdingDays));
  if (explicit && fromDays) {
    return explicit < fromDays ? explicit : fromDays;
  }
  return explicit ?? fromDays;
}

function previousNavDate(points: Array<{ date: string }>, before: string) {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].date < before) {
      return points[index].date;
    }
  }
  return null;
}

export function isPositionTransaction(tx: FundTransaction) {
  if (INACTIVE_STATUS.has(tx.status)) {
    return false;
  }
  if (tx.status === "confirmed") {
    return true;
  }
  return tx.status === "pending" && tx.in_progress !== true && tx.shares_delta != null;
}

function signedSharesDelta(tx: FundTransaction, navOnDate: number | null) {
  if (tx.shares_delta != null && Number.isFinite(tx.shares_delta) && tx.shares_delta !== 0) {
    const magnitude = Math.abs(tx.shares_delta);
    return tx.direction === "sell" ? -magnitude : tx.shares_delta > 0 ? tx.shares_delta : magnitude;
  }
  const nav = tx.nav_on_confirm && tx.nav_on_confirm > 0 ? tx.nav_on_confirm : navOnDate;
  if (nav == null || nav <= 0 || !tx.amount_yuan) {
    return null;
  }
  const shares = tx.amount_yuan / nav;
  return tx.direction === "sell" ? -shares : shares;
}

function buyCostDelta(tx: FundTransaction) {
  return Math.max(0, tx.amount_yuan || 0);
}

export function buildHoldingProfitSeries(
  navPoints: FundNavPoint[],
  transactions: FundTransaction[],
  fallback: HoldingProfitFallback = {},
): HoldingProfitPoint[] {
  const points = [...navPoints]
    .map((point) => ({
      date: normalizeIsoDate(point.date) ?? "",
      nav: point.nav,
    }))
    .filter((point) => point.date && point.nav > 0)
    .sort((left, right) => left.date.localeCompare(right.date));

  if (points.length === 0) {
    return [];
  }

  const ledger = transactions
    .filter(isPositionTransaction)
    .map((tx) => ({
      tx,
      date: normalizeIsoDate(tx.confirm_date) ?? normalizeIsoDate(tx.trade_time),
    }))
    .filter((item): item is { tx: FundTransaction; date: string } => item.date != null)
    .sort((left, right) => {
      const byDate = left.date.localeCompare(right.date);
      if (byDate !== 0) {
        return byDate;
      }
      return left.tx.created_at.localeCompare(right.tx.created_at);
    });

  const navByDate = new Map(points.map((point) => [point.date, point.nav]));
  const txShareSum = ledger.reduce((sum, item) => {
    const delta = signedSharesDelta(item.tx, navByDate.get(item.date) ?? null);
    return sum + (delta ?? 0);
  }, 0);

  const currentShares =
    fallback.shares != null && fallback.shares > 0 ? fallback.shares : null;
  const tolerance =
    currentShares != null ? Math.max(1, Math.abs(currentShares) * 0.02) : 1;
  let baselineShares = 0;
  if (currentShares != null && currentShares - txShareSum > tolerance) {
    baselineShares = currentShares - txShareSum;
  }

  const firstTxDate = ledger[0]?.date ?? null;
  const firstHoldDate =
    inferFirstHoldDate(fallback.firstHoldDate, fallback.holdingDays) ?? firstTxDate;
  let startDate = firstHoldDate ?? points[0].date;
  // 加仓流水盖不住 OCR/已有份额时，购入日常被写成最近成交日。
  // 这类持仓按当前区间净值回补更早的收益，而不是从加仓当天截断。
  if (baselineShares > 0) {
    const addOnOnly = firstTxDate != null && startDate >= firstTxDate;
    if (addOnOnly) {
      startDate = points[0].date;
    } else {
      const prior = previousNavDate(points, startDate);
      if (prior && points.filter((point) => point.date >= startDate).length < 2) {
        startDate = prior;
      }
    }
  }

  let shares = 0;
  let costBasis = 0;
  if (baselineShares > 0) {
    shares = baselineShares;
    const unitCost =
      fallback.unitCost != null && fallback.unitCost > 0
        ? fallback.unitCost
        : points[0].nav;
    costBasis = baselineShares * unitCost;
  }

  let cursor = 0;
  let prevNav: number | null = null;
  let prevShares = 0;
  const series: HoldingProfitPoint[] = [];

  for (const point of points) {
    if (point.date < startDate) {
      prevNav = point.nav;
      continue;
    }

    const dailyProfit =
      prevNav != null && prevNav > 0 && prevShares > 0
        ? round2(prevShares * (point.nav - prevNav))
        : shares > 0 && prevNav != null
          ? 0
          : null;

    while (cursor < ledger.length && ledger[cursor].date <= point.date) {
      const item = ledger[cursor];
      const delta = signedSharesDelta(item.tx, point.nav);
      cursor += 1;
      if (delta == null || delta === 0) {
        continue;
      }
      if (delta > 0) {
        shares += delta;
        costBasis += buyCostDelta(item.tx) || delta * (item.tx.nav_on_confirm || point.nav);
        continue;
      }
      const sold = Math.min(-delta, shares);
      if (shares > 0 && sold > 0) {
        const unit = costBasis / shares;
        shares -= sold;
        costBasis -= sold * unit;
      }
    }

    shares = Math.max(0, shares);
    costBasis = Math.max(0, costBasis);
    if (shares <= 0 && series.length === 0) {
      prevNav = point.nav;
      prevShares = 0;
      continue;
    }

    const marketValue = round2(shares * point.nav);
    const cumulativeProfit = round2(marketValue - costBasis);
    series.push({
      date: point.date,
      nav: point.nav,
      shares: round4(shares),
      costBasis: round2(costBasis),
      marketValue,
      dailyProfit,
      cumulativeProfit,
      holdingReturnPercent:
        costBasis > 0 ? round2((cumulativeProfit / costBasis) * 100) : null,
    });
    prevNav = point.nav;
    prevShares = shares;
  }

  return alignSeriesToCurrent(series, fallback);
}

function alignSeriesToCurrent(
  series: HoldingProfitPoint[],
  fallback: HoldingProfitFallback,
) {
  if (series.length === 0) {
    return series;
  }
  const latest = series[series.length - 1];
  let profitOffset = 0;
  if (fallback.currentProfit != null && Number.isFinite(fallback.currentProfit)) {
    profitOffset = fallback.currentProfit - latest.cumulativeProfit;
  }
  if (Math.abs(profitOffset) < 0.005 && fallback.currentReturnPercent == null) {
    return series;
  }

  return series.map((point) => {
    const cumulativeProfit = round2(point.cumulativeProfit + profitOffset);
    const holdingReturnPercent =
      fallback.currentReturnPercent != null && point.date === latest.date
        ? round2(fallback.currentReturnPercent)
        : point.costBasis > 0
          ? round2((cumulativeProfit / point.costBasis) * 100)
          : point.holdingReturnPercent;
    return {
      ...point,
      cumulativeProfit,
      holdingReturnPercent,
    };
  });
}

export function sliceHoldingProfitSeries(points: HoldingProfitPoint[], days: number) {
  if (days <= 0 || points.length <= days) {
    return points;
  }
  return points.slice(-days);
}

export function periodProfitChange(points: HoldingProfitPoint[]) {
  if (points.length === 0) {
    return null;
  }
  if (points.length === 1) {
    return points[0].cumulativeProfit;
  }
  return round2(points[points.length - 1].cumulativeProfit - points[0].cumulativeProfit);
}

export function toChartSeries(points: HoldingProfitPoint[]) {
  return points.map((point) => ({
    date: point.date,
    nav: point.nav,
    dailyReturn: point.holdingReturnPercent,
    fundPercent: point.cumulativeProfit,
    benchPercent: null,
  }));
}
