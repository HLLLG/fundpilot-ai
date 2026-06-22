import type { Holding } from "@/lib/api";
import {
  computeDailyProfit,
  computeEstimatedDailyReturnPercent,
  computeEstimatedHoldingReturnPercent,
  computeHoldingProfit,
  dailyProfitIsEstimated,
  holdingDailyReturnIsEstimated,
  holdingProfitIsEstimated,
} from "@/lib/holdingMetrics";

/** 展示层口径：优先使用后端 serialize_holding_for_client 写入的字段。 */
export function getEstimatedHoldingReturnPercent(holding: Holding): number | null {
  if (holding.estimated_holding_return_percent != null) {
    return holding.estimated_holding_return_percent;
  }
  return computeEstimatedHoldingReturnPercent(holding);
}

export function getEstimatedHoldingProfit(holding: Holding): number | null {
  if (holding.estimated_holding_profit != null) {
    return holding.estimated_holding_profit;
  }
  return computeHoldingProfit(holding);
}

export function getEstimatedDailyReturnPercent(holding: Holding): number | null {
  if (holding.estimated_daily_return_percent != null) {
    return holding.estimated_daily_return_percent;
  }
  return computeEstimatedDailyReturnPercent(holding);
}

export function isHoldingReturnEstimated(holding: Holding): boolean {
  if (holding.holding_return_is_estimated != null) {
    return holding.holding_return_is_estimated;
  }
  return holdingProfitIsEstimated(holding);
}

export function isDailyReturnEstimated(holding: Holding): boolean {
  if (holding.daily_return_is_estimated != null) {
    return holding.daily_return_is_estimated;
  }
  return holdingDailyReturnIsEstimated(holding);
}

export function isDailyProfitEstimated(holding: Holding): boolean {
  if (holding.daily_return_is_estimated === false) {
    return false;
  }
  return dailyProfitIsEstimated(holding);
}

export function getDailyProfit(holding: Holding): number | null {
  if (holding.daily_profit != null) {
    return holding.daily_profit;
  }
  return computeDailyProfit(holding);
}

/** 养基宝口径：持有金额展示上一交易日结算值 */
export function getSettledHoldingAmount(holding: Holding): number {
  if (holding.display_holding_amount != null) {
    return holding.display_holding_amount;
  }
  if (holding.settled_holding_amount != null) {
    return holding.settled_holding_amount;
  }
  return holding.holding_amount;
}
