import type { Holding } from "@/lib/api";

const TEST_FUND_CODES = new Set(["000001"]);
const TEST_NAME_PREFIXES = ["测试", "新基金"];

export function isTestHolding(holding: Holding): boolean {
  if (TEST_FUND_CODES.has(holding.fund_code)) {
    return true;
  }
  const name = (holding.fund_name || "").trim();
  return TEST_NAME_PREFIXES.some((prefix) => name.startsWith(prefix));
}

export function withoutTestHoldings(holdings: Holding[]): Holding[] {
  return holdings.filter((holding) => !isTestHolding(holding));
}

function round2(value: number) {
  return Math.round(value * 100) / 100;
}

/** 持有收益率（优先 holding_return_percent） */
export function resolveHoldingReturnPercent(holding: Holding): number | null {
  if (holding.holding_return_percent != null) {
    return holding.holding_return_percent;
  }
  if (holding.return_percent != null) {
    return holding.return_percent;
  }
  return null;
}

/**
 * 持有收益额：优先 OCR；否则由 持有金额 × 持有收益率 反推。
 * 公式：profit = amount × r / (100 + r)，r 为持有收益率（相对成本）。
 */
export function computeHoldingProfit(holding: Holding): number | null {
  if (holding.holding_profit != null) {
    return holding.holding_profit;
  }
  const returnPercent = resolveHoldingReturnPercent(holding);
  if (returnPercent == null || holding.holding_amount <= 0) {
    return null;
  }
  return round2((holding.holding_amount * returnPercent) / (100 + returnPercent));
}

/**
 * 板块刷新后重算当日收益（忽略 OCR 截图里的当日收益）。
 * 公式：当日收益 ≈ 持有金额 × 板块涨跌%
 */
export function applySectorDailyEstimate(holding: Holding): Holding {
  const sector = holding.sector_return_percent;
  if (sector == null || holding.holding_amount <= 0) {
    return {
      ...holding,
      daily_profit: null,
      daily_return_percent: null,
    };
  }
  return {
    ...holding,
    daily_profit: round2((holding.holding_amount * sector) / 100),
    daily_return_percent: sector,
  };
}

/**
 * 当日收益额：优先已写入的 daily_profit；否则用板块涨跌估算（展示层 fallback）。
 */
export function computeDailyProfit(holding: Holding): number | null {
  if (holding.daily_profit != null) {
    return holding.daily_profit;
  }
  if (holding.sector_return_percent != null && holding.holding_amount > 0) {
    return round2((holding.holding_amount * holding.sector_return_percent) / 100);
  }
  return null;
}

export function computeEstimatedDailyReturnPercent(holding: Holding): number | null {
  if (holding.daily_return_percent != null) {
    return holding.daily_return_percent;
  }
  if (holding.sector_return_percent == null) {
    return null;
  }
  const settled = resolveHoldingReturnPercent(holding);
  if (settled == null) {
    return null;
  }
  return round2(holding.sector_return_percent + settled);
}

export function holdingDailyReturnIsEstimated(holding: Holding): boolean {
  return (
    holding.daily_return_percent == null &&
    holding.sector_return_percent != null &&
    computeEstimatedDailyReturnPercent(holding) != null
  );
}

export function dailyProfitIsEstimated(holding: Holding): boolean {
  return holding.sector_return_percent != null && holding.holding_amount > 0;
}

export function holdingProfitIsEstimated(holding: Holding): boolean {
  return holding.holding_profit == null && computeHoldingProfit(holding) != null;
}

/** 板块刷新后补全持有收益等（当日收益由 applySectorDailyEstimate 负责） */
export function enrichHoldingComputedFields(holding: Holding): Holding {
  const withDaily = applySectorDailyEstimate(holding);
  const holdingReturn = resolveHoldingReturnPercent(withDaily);
  const holdingProfit = computeHoldingProfit(withDaily);
  return {
    ...withDaily,
    holding_return_percent: holdingReturn,
    holding_profit: holdingProfit,
  };
}

export function sumDailyProfit(holdings: Holding[]): number {
  return round2(
    holdings.reduce((sum, holding) => sum + (computeDailyProfit(holding) ?? 0), 0),
  );
}

export function sumHoldingAmount(holdings: Holding[]): number {
  return round2(holdings.reduce((sum, holding) => sum + holding.holding_amount, 0));
}

/** 持仓成本总额 = 持有金额 / (1 + 持有收益率) */
export function computeCostBasis(holding: Holding): number | null {
  const returnPercent = resolveHoldingReturnPercent(holding);
  if (returnPercent == null || holding.holding_amount <= 0) {
    return null;
  }
  return round2(holding.holding_amount / (1 + returnPercent / 100));
}

/** 持仓占比（相对账户总资产） */
export function computeHoldingWeight(
  holding: Holding,
  totalAssets: number | null | undefined,
): number | null {
  if (!totalAssets || totalAssets <= 0 || holding.holding_amount <= 0) {
    return null;
  }
  return round2((holding.holding_amount / totalAssets) * 100);
}

export function formatPlainMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPlainPercent(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value.toFixed(2)}%`;
}

export function formatSignedMoney(value: number | null | undefined, options?: { plus?: boolean }) {
  if (value === null || value === undefined) {
    return "—";
  }
  const prefix = options?.plus !== false && value > 0 ? "+" : "";
  return `${prefix}${value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatSignedPercent(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** A 股习惯：涨红跌绿 */
export function cnProfitClass(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "text-rose-600" : "text-emerald-600";
}
