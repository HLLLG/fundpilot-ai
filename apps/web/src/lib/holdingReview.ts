import type { Holding, HoldingFieldWarning, HoldingListDiff, PortfolioSummary } from "@/lib/api";

export function holdingsHaveSettledDaily(holdings: Holding[]): boolean {
  return holdings.some(
    (holding) => holding.daily_profit != null || holding.daily_return_percent != null,
  );
}

export function resolveDailyProfitSource(
  summary: PortfolioSummary | null,
  holdings: Holding[],
): PortfolioSummary["daily_profit_source"] {
  if (summary?.daily_profit == null) {
    return null;
  }
  if (summary.daily_profit_source) {
    return summary.daily_profit_source;
  }
  if (!holdingsHaveSettledDaily(holdings)) {
    return "penetration_estimate";
  }
  return "settled";
}

export function canAllocatePenetrationDaily(
  summary: PortfolioSummary | null,
  holdings: Holding[],
): boolean {
  if (!holdings.length || summary?.daily_profit == null) {
    return false;
  }
  return resolveDailyProfitSource(summary, holdings) === "penetration_estimate";
}

export function isPenetrationEstimateDisplay(
  summary: PortfolioSummary | null,
  holdings: Holding[],
): boolean {
  return resolveDailyProfitSource(summary, holdings) === "penetration_estimate";
}

export function mergeHoldingsWithPrevious(previous: Holding[], current: Holding[]): Holding[] {
  if (!previous.length) {
    return [...current];
  }

  const used = new Set<number>();
  const merged: Holding[] = [];

  const normalizeName = (name: string) => name.replace(/[\s.·…]+/g, "").toLowerCase();

  for (const prev of previous) {
    const prevName = normalizeName(prev.fund_name);
    const matchIndex = current.findIndex((item, index) => {
      if (used.has(index)) {
        return false;
      }
      if (prev.fund_code !== "000000" && item.fund_code === prev.fund_code) {
        return true;
      }
      const itemName = normalizeName(item.fund_name);
      return itemName === prevName || itemName.includes(prevName) || prevName.includes(itemName);
    });

    if (matchIndex < 0) {
      merged.push(prev);
      continue;
    }

    used.add(matchIndex);
    const cur = current[matchIndex];
    merged.push({
      ...prev,
      holding_amount: cur.holding_amount,
      daily_profit: cur.daily_profit,
      daily_return_percent: cur.daily_return_percent,
      holding_profit: cur.holding_profit,
      holding_return_percent: cur.holding_return_percent ?? cur.return_percent,
      return_percent: cur.holding_return_percent ?? cur.return_percent ?? prev.return_percent,
      sector_name: cur.sector_name ?? prev.sector_name,
      sector_return_percent: cur.sector_return_percent,
      fund_code: prev.fund_code !== "000000" ? prev.fund_code : cur.fund_code,
      fund_name: prev.fund_name.length >= cur.fund_name.length ? prev.fund_name : cur.fund_name,
    });
  }

  current.forEach((item, index) => {
    if (!used.has(index)) {
      merged.push(item);
    }
  });

  return merged;
}

export function warningsForCell(
  warnings: HoldingFieldWarning[],
  index: number,
  field: string,
): HoldingFieldWarning | undefined {
  return warnings.find((item) => item.index === index && item.field === field);
}

export function globalWarnings(warnings: HoldingFieldWarning[]): HoldingFieldWarning[] {
  return warnings.filter((item) => item.index < 0);
}

export function accountInfoWarnings(warnings: HoldingFieldWarning[]): HoldingFieldWarning[] {
  return globalWarnings(warnings).filter((item) => item.severity === "info");
}

export function accountActionWarnings(warnings: HoldingFieldWarning[]): HoldingFieldWarning[] {
  return globalWarnings(warnings).filter((item) => item.severity !== "info");
}

export function diffForRow(diffs: HoldingListDiff[], index: number): HoldingListDiff | undefined {
  return diffs.find((item) => item.index === index && item.change_type !== "unchanged");
}

export function countActionableWarnings(warnings: HoldingFieldWarning[]): number {
  return warnings.filter((item) => item.severity === "error" || item.severity === "warn").length;
}
