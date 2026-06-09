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

/** OCR 草稿占位行（000000 / 待录入），不在账户汇总与日报中展示。 */
export function isPlaceholderHolding(holding: Holding): boolean {
  if (holding.fund_code === "000000") {
    return true;
  }
  const name = (holding.fund_name || "").trim();
  return name === "待录入基金" || name.startsWith("待录入");
}

/** 账户汇总 / 生成日报使用的有效持仓列表。 */
export function displayableHoldings(holdings: Holding[]): Holding[] {
  return withoutTestHoldings(holdings).filter((holding) => !isPlaceholderHolding(holding));
}

function round2(value: number) {
  return Math.round(value * 100) / 100;
}

/** 持有收益率（昨日结算，不含今日涨跌） */
export function resolveHoldingReturnPercent(holding: Holding): number | null {
  if (holding.holding_return_percent != null) {
    return holding.holding_return_percent;
  }
  if (holding.return_percent != null) {
    return holding.return_percent;
  }
  return null;
}

/** 当日涨跌分量：官方净值已公布时用净值，否则用关联板块涨跌。 */
export function resolveIntradayReturnPercent(holding: Holding): number | null {
  if (
    holding.daily_return_percent_source === "official_nav" &&
    holding.daily_return_percent != null
  ) {
    return holding.daily_return_percent;
  }
  if (holding.sector_return_percent != null) {
    return holding.sector_return_percent;
  }
  if (holding.daily_return_percent != null) {
    return holding.daily_return_percent;
  }
  return null;
}

/**
 * 持有收益率展示：
 * - 官方净值已公布：OCR/档案中的持有收益率已是含当日的总值
 * - 盘中/净值未公布：昨日结算 + 板块涨跌估算
 */
export function computeEstimatedHoldingReturnPercent(holding: Holding): number | null {
  const settled = resolveHoldingReturnPercent(holding);
  if (holding.daily_return_percent_source === "official_nav") {
    if (settled != null) {
      return round2(settled);
    }
    if (holding.daily_return_percent != null) {
      return round2(holding.daily_return_percent);
    }
    return null;
  }
  const intraday = resolveIntradayReturnPercent(holding);
  if (settled == null) {
    return null;
  }
  if (intraday == null) {
    return settled;
  }
  return round2(settled + intraday);
}

function resolveSettledHoldingProfit(holding: Holding): number | null {
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
 * 持有收益额展示：
 * - 官方净值已公布：OCR/档案中的持有收益已是含当日的总值
 * - 盘中/净值未公布：昨日结算持有收益 + 当日收益（板块估算）
 */
export function computeHoldingProfit(holding: Holding): number | null {
  if (holding.daily_return_percent_source === "official_nav") {
    if (holding.holding_profit != null) {
      return holding.holding_profit;
    }
    const totalReturn = computeEstimatedHoldingReturnPercent(holding);
    if (totalReturn != null && holding.holding_amount > 0) {
      return round2((holding.holding_amount * totalReturn) / (100 + totalReturn));
    }
    return null;
  }
  const settledProfit = resolveSettledHoldingProfit(holding);
  const dailyProfit = computeDailyProfit(holding);
  if (settledProfit != null && dailyProfit != null) {
    return round2(settledProfit + dailyProfit);
  }
  if (settledProfit != null) {
    return settledProfit;
  }
  const estimatedReturn = computeEstimatedHoldingReturnPercent(holding);
  if (estimatedReturn == null || holding.holding_amount <= 0) {
    return null;
  }
  return round2((holding.holding_amount * estimatedReturn) / (100 + estimatedReturn));
}

/** 持有金额是否已含当日涨跌（份额×净值同步后） */
export function holdingAmountIncludesTodayReturn(holding: Holding): boolean {
  if (holding.amount_includes_today != null) {
    return holding.amount_includes_today;
  }
  return holding.daily_return_percent_source === "official_nav";
}

export function computeDailyProfitFromRate(
  holdingAmount: number,
  dailyReturnPercent: number,
  amountIncludesToday: boolean,
): number {
  if (amountIncludesToday) {
    return computeOfficialDailyProfit(holdingAmount, dailyReturnPercent);
  }
  return round2((holdingAmount * dailyReturnPercent) / 100);
}

/**
 * 板块刷新后重算当日收益（忽略 OCR 截图里的当日收益）。
 * 若后端已写入官方净值当日收益率，则保留。
 */
export function applySectorDailyEstimate(holding: Holding): Holding {
  if (holding.daily_return_percent_source === "official_nav") {
    return holding;
  }
  const sector = holding.sector_return_percent;
  if (sector == null || holding.holding_amount <= 0) {
    return {
      ...holding,
      daily_profit: null,
      daily_return_percent: null,
      daily_return_percent_source: null,
    };
  }
  const includesToday = holdingAmountIncludesTodayReturn(holding);
  return {
    ...holding,
    daily_profit: computeDailyProfitFromRate(holding.holding_amount, sector, includesToday),
    daily_return_percent: sector,
    daily_return_percent_source: "sector_estimate",
  };
}

/**
 * 当日收益额：优先已写入的 daily_profit；官方净值或板块涨跌估算。
 */
/** 官方净值当日收益：结算前金额 × 日涨幅 = 现金额 × r / (100 + r)。 */
export function computeOfficialDailyProfit(
  holdingAmount: number,
  dailyReturnPercent: number,
): number {
  return round2((holdingAmount * dailyReturnPercent) / (100 + dailyReturnPercent));
}

/** 昨日收益：由后端写入上一交易日官方净值收益，或 OCR 兜底。 */
export function computeYesterdayProfit(holding: Holding): number | null {
  return holding.yesterday_profit ?? null;
}

export function computeDailyProfit(holding: Holding): number | null {
  const amount = holding.holding_amount;
  if (amount <= 0) {
    return holding.daily_profit ?? null;
  }

  const includesToday = holdingAmountIncludesTodayReturn(holding);
  if (holding.daily_return_percent != null) {
    return computeDailyProfitFromRate(
      amount,
      holding.daily_return_percent,
      includesToday || holding.daily_return_percent_source === "official_nav",
    );
  }

  if (holding.sector_return_percent != null) {
    return computeDailyProfitFromRate(amount, holding.sector_return_percent, includesToday);
  }

  return holding.daily_profit ?? null;
}

/** 关联板块列：始终展示东财板块/指数涨跌，不用官方净值。 */
export function resolveSectorBoardReturnPercent(holding: Holding): number | null {
  return holding.sector_return_percent ?? null;
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
  if (holding.daily_return_percent_source === "official_nav") {
    return false;
  }
  return (
    holding.daily_return_percent == null &&
    holding.sector_return_percent != null &&
    computeEstimatedDailyReturnPercent(holding) != null
  );
}

export function dailyProfitIsEstimated(holding: Holding): boolean {
  if (holding.daily_return_percent_source === "official_nav") {
    return false;
  }
  if (holding.daily_profit != null) {
    return false;
  }
  return holding.sector_return_percent != null && holding.holding_amount > 0;
}

export function holdingProfitIsEstimated(holding: Holding): boolean {
  if (holding.daily_return_percent_source === "official_nav") {
    return holding.holding_profit == null;
  }
  if (resolveIntradayReturnPercent(holding) != null) {
    return true;
  }
  return holding.holding_profit == null && computeHoldingProfit(holding) != null;
}

/** 板块刷新后补全可持久化字段（当日收益由 applySectorDailyEstimate 负责） */
export function enrichHoldingComputedFields(holding: Holding): Holding {
  const withDaily = applySectorDailyEstimate(holding);
  const holdingReturn = resolveHoldingReturnPercent(withDaily);
  const holdingProfit = resolveSettledHoldingProfit(withDaily);
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
