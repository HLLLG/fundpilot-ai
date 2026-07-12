/**
 * 持有列 fallback 计算（离线/缓存无 display 字段时用）。
 * 权威口径：apps/api/app/services/holding_estimates.py + holding_client.serialize_holding_for_client
 * 展示请优先用 holdingDisplay.ts 的 getter。
 */
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
  return withoutTestHoldings(holdings).filter((holding) => {
    if (isPlaceholderHolding(holding)) {
      return false;
    }
    const amount =
      holding.settled_holding_amount != null
        ? holding.settled_holding_amount
        : holding.holding_amount;
    return (amount ?? 0) > 0;
  });
}

/** OCR / 快速 apply 响应可能缺少板块与收益字段；刷新完成前保留上一屏展示数据。 */
export const HOLDING_QUOTE_FIELDS = [
  "sector_return_percent",
  "sector_return_percent_source",
  "daily_profit",
  "daily_return_percent",
  "daily_return_percent_source",
  "yesterday_profit",
  "sector_name",
  "intraday_index_name",
  "estimated_holding_return_percent",
  "estimated_holding_profit",
  "estimated_daily_return_percent",
  "holding_return_is_estimated",
  "daily_return_is_estimated",
] as const satisfies readonly (keyof Holding)[];

const PRESERVE_QUOTE_FIELDS = HOLDING_QUOTE_FIELDS;

export function stripHoldingsQuoteFields(holdings: Holding[]): Holding[] {
  return holdings.map((holding) => {
    const stripped: Holding = { ...holding };
    for (const key of HOLDING_QUOTE_FIELDS) {
      if (key in stripped) {
        delete stripped[key];
      }
    }
    return stripped;
  });
}

/** apply / OCR 确认：用持久化持有收益同步展示字段，避免保留上一屏 estimated 污染。 */
export function seedApplyDisplayFields(holding: Holding): Holding {
  const next: Holding = { ...holding };
  if (holding.holding_profit != null) {
    next.estimated_holding_profit =
      holding.estimated_holding_profit ?? holding.holding_profit;
    next.holding_return_is_estimated = holding.holding_return_is_estimated ?? false;
  }
  if (holding.holding_return_percent != null) {
    next.estimated_holding_return_percent =
      holding.estimated_holding_return_percent ?? holding.holding_return_percent;
  }
  if (
    holding.daily_profit != null
    && holding.daily_return_percent_source === "official_nav"
  ) {
    next.daily_return_is_estimated = holding.daily_return_is_estimated ?? false;
  }
  return next;
}

export function withApplyDisplayFields(holdings: Holding[]): Holding[] {
  return holdings.map(seedApplyDisplayFields);
}

function mergeHoldingQuoteFields(previous: Holding, incoming: Holding): Holding {
  const merged: Holding = { ...incoming };
  for (const key of PRESERVE_QUOTE_FIELDS) {
    const nextValue = incoming[key];
    const prevValue = previous[key];
    if ((nextValue === null || nextValue === undefined) && prevValue !== null && prevValue !== undefined) {
      (merged as Record<keyof Holding, Holding[keyof Holding]>)[key] = prevValue;
    }
  }
  if (incoming.estimated_holding_profit != null) {
    merged.estimated_holding_profit = incoming.estimated_holding_profit;
    merged.holding_return_is_estimated = incoming.holding_return_is_estimated ?? false;
  } else if (incoming.holding_profit != null) {
    merged.estimated_holding_profit = incoming.holding_profit;
    merged.holding_return_is_estimated = incoming.holding_return_is_estimated ?? false;
  }
  if (incoming.estimated_holding_return_percent != null) {
    merged.estimated_holding_return_percent = incoming.estimated_holding_return_percent;
  } else if (incoming.holding_return_percent != null) {
    merged.estimated_holding_return_percent = incoming.holding_return_percent;
  }
  return merged;
}

export function mergeHoldingsPreserveQuoteFields(
  previous: Holding[],
  incoming: Holding[],
): Holding[] {
  if (!previous.length) {
    return incoming;
  }
  const prevByCode = new Map(
    previous
      .filter((item) => item.fund_code && item.fund_code !== "000000")
      .map((item) => [item.fund_code, item] as const),
  );
  const prevByName = new Map(
    previous.map((item) => [normalizeHoldingName(item.fund_name || ""), item] as const),
  );
  return incoming.map((item) => {
    const prev =
      (item.fund_code && item.fund_code !== "000000" ? prevByCode.get(item.fund_code) : undefined) ??
      prevByName.get(normalizeHoldingName(item.fund_name || ""));
    if (!prev) {
      return item;
    }
    return mergeHoldingQuoteFields(prev, item);
  });
}

export type HoldingIdentity = Pick<Holding, "fund_code" | "fund_name">;

function normalizeHoldingName(name: string): string {
  return name.replace(/\.\.\./g, "").replace(/[.\s·]/g, "").trim();
}

export function holdingIdentityKey(holding: HoldingIdentity): string {
  const code = (holding.fund_code || "").trim();
  if (code && code !== "000000") {
    return code;
  }
  return normalizeHoldingName(holding.fund_name || "");
}

/** 同 fund_code / 同名合并为一条，避免导航或 hydrate 产生重复行。 */
export function dedupeHoldingsByCode(holdings: Holding[]): Holding[] {
  const byKey = new Map<string, Holding>();
  const order: string[] = [];
  for (const item of holdings) {
    const key = holdingIdentityKey(item);
    if (!key) {
      continue;
    }
    if (!byKey.has(key)) {
      order.push(key);
    }
    const existing = byKey.get(key);
    byKey.set(key, existing ? mergeHoldingQuoteFields(existing, item) : item);
  }
  return order.map((key) => byKey.get(key)!);
}

export function navigableHoldings(holdings: Holding[]): Holding[] {
  return dedupeHoldingsByCode(displayableHoldings(holdings));
}

export function patchHoldingRecord(holdings: Holding[], patch: Holding): Holding[] {
  const index = findHoldingIndex(holdings, patch);
  if (index < 0) {
    return holdings;
  }
  const current = holdings[index];
  const patchCode = (patch.fund_code || "").trim();
  const currentCode = (current.fund_code || "").trim();
  if (
    patchCode
    && patchCode !== "000000"
    && currentCode
    && currentCode !== "000000"
    && patchCode !== currentCode
  ) {
    return holdings;
  }
  return holdings.map((item, itemIndex) =>
    itemIndex === index ? mergeHoldingQuoteFields(item, patch) : item,
  );
}

/** @deprecated Prefer patchHoldingRecord; dedupe on hydrate removed to avoid losing funds. */
export function updateHoldingAtIndex(
  holdings: Holding[],
  index: number,
  patch: Holding,
): Holding[] {
  if (index < 0 || index >= holdings.length) {
    return holdings;
  }
  return patchHoldingRecord(holdings, patch);
}

/** 分批截图录入：保留已有持仓，同码/同名用新 OCR 覆盖金额与收益，否则追加。 */
export function mergeHoldingsAppend(
  previous: Holding[],
  incoming: Holding[],
): Holding[] {
  const merged = [...displayableHoldings(previous)];
  for (const item of incoming) {
    const idx = findHoldingIndex(merged, item);
    if (idx >= 0) {
      merged[idx] = mergeHoldingQuoteFields(merged[idx], item);
    } else {
      merged.push(item);
    }
  }
  return dedupeHoldingsByCode(merged);
}

/** 在完整持仓数组中定位基金；优先 fund_code，避免排序/刷新后下标错位。 */
export function findHoldingIndex(
  holdings: Holding[],
  target: HoldingIdentity,
): number {
  const code = (target.fund_code || "").trim();
  if (code && code !== "000000") {
    const byCode = holdings.findIndex((item) => item.fund_code === code);
    if (byCode >= 0) {
      return byCode;
    }
  }
  const targetName = normalizeHoldingName(target.fund_name || "");
  if (targetName) {
    const byName = holdings.findIndex(
      (item) => normalizeHoldingName(item.fund_name || "") === targetName,
    );
    if (byName >= 0) {
      return byName;
    }
  }
  return holdings.findIndex(
    (item) =>
      item.fund_code === target.fund_code && item.fund_name === target.fund_name,
  );
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
  const repaired = repairCorruptedSettledProfit(holding);
  const settled = resolveHoldingReturnPercent(repaired);
  if (ocrHoldingProfitIsCumulative(repaired)) {
    return settled != null ? round2(settled) : null;
  }
  if (holding.daily_return_percent_source === "official_nav") {
    if (settled != null) {
      return round2(settled);
    }
    if (holding.daily_return_percent != null) {
      return round2(holding.daily_return_percent);
    }
    return null;
  }
  const intraday = resolveIntradayReturnPercent(repaired);
  if (settled == null) {
    return null;
  }
  if (intraday == null) {
    return settled;
  }
  return round2(settled + intraday);
}

function expectedSettledProfit(holding: Holding, returnPercent: number): number | null {
  if (holding.holding_amount <= 0) {
    return null;
  }
  return round2((holding.holding_amount * returnPercent) / (100 + returnPercent));
}

/** 支付宝 OCR：持有收益与收益率相对当前金额自洽，已是含当日的累计值。 */
function ocrHoldingProfitIsCumulative(holding: Holding): boolean {
  if (holding.holding_profit == null) {
    return false;
  }
  const returnPercent = resolveHoldingReturnPercent(holding);
  const amount =
    holding.settled_holding_amount ??
    holding.display_holding_amount ??
    holding.holding_amount;
  if (returnPercent == null || amount <= 0) {
    return false;
  }
  const expected = round2((amount * returnPercent) / (100 + returnPercent));
  if (expected === 0) {
    return false;
  }
  return Math.abs(holding.holding_profit - expected) <= Math.max(1, Math.abs(expected) * 0.02);
}

function repairCorruptedSettledProfit(holding: Holding): Holding {
  if (holding.holding_profit == null) {
    return holding;
  }
  const profileReturn = holding.holding_return_percent ?? holding.return_percent;
  const referenceReturn = profileReturn;
  if (referenceReturn == null) {
    return holding;
  }
  const expected = expectedSettledProfit(holding, referenceReturn);
  if (expected == null) {
    return holding;
  }
  const delta = Math.abs(holding.holding_profit - expected);
  if (delta <= Math.max(25, Math.abs(expected) * 0.35)) {
    return holding;
  }
  return {
    ...holding,
    holding_profit: expected,
    holding_return_percent: referenceReturn,
    return_percent: referenceReturn,
  };
}

function resolveSettledHoldingProfit(holding: Holding): number | null {
  const repaired = repairCorruptedSettledProfit(holding);
  if (repaired.holding_profit != null) {
    return repaired.holding_profit;
  }
  const returnPercent = resolveHoldingReturnPercent(repaired);
  if (returnPercent == null) {
    return null;
  }
  return expectedSettledProfit(repaired, returnPercent);
}

/**
 * 持有收益额展示：
 * - 官方净值已公布：OCR/档案中的持有收益已是含当日的总值
 * - 盘中/净值未公布：昨日结算持有收益 + 当日收益（板块估算）
 */
export function computeHoldingProfit(holding: Holding): number | null {
  const repaired = repairCorruptedSettledProfit(holding);
  if (ocrHoldingProfitIsCumulative(repaired)) {
    return repaired.holding_profit ?? null;
  }
  if (repaired.daily_return_percent_source === "official_nav") {
    if (repaired.holding_profit != null) {
      return repaired.holding_profit;
    }
    const totalReturn = computeEstimatedHoldingReturnPercent(repaired);
    if (totalReturn != null && repaired.holding_amount > 0) {
      return round2((repaired.holding_amount * totalReturn) / (100 + totalReturn));
    }
    return null;
  }
  const settledProfit = resolveSettledHoldingProfit(repaired);
  const dailyProfit = computeDailyProfit(repaired);
  if (settledProfit != null && dailyProfit != null) {
    return round2(settledProfit + dailyProfit);
  }
  if (settledProfit != null) {
    return settledProfit;
  }
  const estimatedReturn = computeEstimatedHoldingReturnPercent(repaired);
  if (estimatedReturn == null || repaired.holding_amount <= 0) {
    return null;
  }
  return round2((repaired.holding_amount * estimatedReturn) / (100 + estimatedReturn));
}

/** 持有金额是否已含当日涨跌（份额×净值同步后） */
export function holdingAmountIncludesTodayReturn(holding: Holding): boolean {
  if (holding.amount_includes_today != null) {
    return holding.amount_includes_today;
  }
  return false;
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
  if (
    holding.profit_accrual_deferred ||
    holding.daily_return_percent_source === "pending_accrual"
  ) {
    return {
      ...holding,
      daily_profit: 0,
      daily_return_percent: 0,
      daily_return_percent_source: "pending_accrual",
    };
  }
  if (holding.daily_return_percent_source === "official_nav") {
    return holding;
  }
  const sector = holding.sector_return_percent;
  const amount =
    holding.settled_holding_amount ?? holding.display_holding_amount ?? holding.holding_amount;
  if (sector == null || amount <= 0) {
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
    daily_profit: computeDailyProfitFromRate(amount, sector, includesToday),
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
  if (
    holding.profit_accrual_deferred ||
    holding.daily_return_percent_source === "pending_accrual"
  ) {
    return 0;
  }
  const amount =
    holding.settled_holding_amount ?? holding.display_holding_amount ?? holding.holding_amount;
  if (amount <= 0) {
    return holding.daily_profit ?? null;
  }

  const includesToday = holdingAmountIncludesTodayReturn(holding);
  if (holding.daily_return_percent != null) {
    if (holding.daily_return_percent_source === "official_nav" && !includesToday) {
      return round2((amount * holding.daily_return_percent) / 100);
    }
    return computeDailyProfitFromRate(amount, holding.daily_return_percent, includesToday);
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

export function mergeSectorIntradayClose(
  holding: Holding,
  closeChangePercent: number | null | undefined,
): Holding {
  if (closeChangePercent === null || closeChangePercent === undefined) {
    return holding;
  }
  const rounded = round2(closeChangePercent);
  if (holding.sector_return_percent === rounded) {
    return holding;
  }
  return {
    ...holding,
    sector_return_percent: rounded,
    sector_return_percent_source: "closing_estimate",
  };
}

export function computeEstimatedDailyReturnPercent(holding: Holding): number | null {
  if (holding.daily_return_percent != null) {
    return holding.daily_return_percent;
  }
  if (holding.sector_return_percent == null) {
    return null;
  }
  return round2(holding.sector_return_percent);
}

export function holdingDailyReturnIsEstimated(holding: Holding): boolean {
  if (
    holding.daily_return_percent_source === "official_nav" ||
    holding.daily_return_percent_source === "pending_accrual" ||
    holding.profit_accrual_deferred
  ) {
    return false;
  }
  return holding.daily_return_percent == null && holding.sector_return_percent != null;
}

export function dailyProfitIsEstimated(holding: Holding): boolean {
  if (
    holding.daily_return_percent_source === "official_nav" ||
    holding.daily_return_percent_source === "pending_accrual" ||
    holding.profit_accrual_deferred
  ) {
    return false;
  }
  if (holding.daily_profit != null) {
    return false;
  }
  return holding.sector_return_percent != null && holding.holding_amount > 0;
}

export function holdingProfitIsEstimated(holding: Holding): boolean {
  if (
    holding.daily_return_percent_source === "pending_accrual" ||
    holding.profit_accrual_deferred
  ) {
    return false;
  }
  if (ocrHoldingProfitIsCumulative(holding)) {
    return false;
  }
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

/** 总资产 = Σ(结算持有金额 + 当日收益)；盘中结算额不变，总资产随估算收益动 */
export function sumPortfolioTotalAssets(holdings: Holding[]): number {
  return round2(
    holdings.reduce((sum, holding) => {
      const settled =
        holding.settled_holding_amount ??
        holding.display_holding_amount ??
        holding.holding_amount;
      return sum + settled + (computeDailyProfit(holding) ?? 0);
    }, 0),
  );
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
  return value > 0 ? "profit-up" : "profit-down";
}
