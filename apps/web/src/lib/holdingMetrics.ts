import type { Holding } from "@/lib/api";

export function computeEstimatedDailyReturnPercent(holding: Holding): number | null {
  if (holding.daily_return_percent != null) {
    return holding.daily_return_percent;
  }
  if (holding.sector_return_percent == null) {
    return null;
  }
  const settled = holding.holding_return_percent ?? holding.return_percent;
  return Math.round((holding.sector_return_percent + settled) * 100) / 100;
}

export function holdingDailyReturnIsEstimated(holding: Holding): boolean {
  return (
    holding.daily_return_percent == null &&
    holding.sector_return_percent != null &&
    computeEstimatedDailyReturnPercent(holding) != null
  );
}
