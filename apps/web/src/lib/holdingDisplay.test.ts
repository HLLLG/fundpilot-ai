import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { Holding } from "@/lib/api";
import {
  getDailyProfit,
  getEstimatedDailyReturnPercent,
  getEstimatedHoldingProfit,
  getEstimatedHoldingReturnPercent,
  isDailyReturnEstimated,
} from "@/lib/holdingDisplay";

type FixtureCase = {
  id: string;
  holding: Holding;
  expected: Record<string, number | boolean>;
};

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../api/tests/fixtures/holding_metrics_cases.json",
);
const cases = JSON.parse(readFileSync(fixturePath, "utf-8")) as FixtureCase[];

describe("holdingDisplay parity with backend fixtures", () => {
  it.each(cases)("$id matches backend when display fields are attached", (caseItem) => {
    const holding = {
      ...caseItem.holding,
      estimated_holding_return_percent:
        caseItem.expected.estimated_holding_return_percent ?? null,
      estimated_holding_profit: caseItem.expected.estimated_holding_profit ?? null,
      holding_return_is_estimated: caseItem.expected.holding_return_is_estimated ?? null,
      estimated_daily_return_percent: caseItem.expected.estimated_daily_return_percent ?? null,
      daily_return_is_estimated: caseItem.expected.daily_return_is_estimated ?? null,
      daily_profit: caseItem.expected.daily_profit ?? caseItem.holding.daily_profit ?? null,
    } as Holding;

    for (const [key, value] of Object.entries(caseItem.expected)) {
      if (key === "estimated_holding_return_percent") {
        expect(getEstimatedHoldingReturnPercent(holding)).toBeCloseTo(value as number, 2);
      } else if (key === "estimated_holding_profit") {
        expect(getEstimatedHoldingProfit(holding)).toBeCloseTo(value as number, 0);
      } else if (key === "estimated_daily_return_percent") {
        expect(getEstimatedDailyReturnPercent(holding)).toBeCloseTo(value as number, 2);
      } else if (key === "daily_return_is_estimated") {
        expect(isDailyReturnEstimated(holding)).toBe(value);
      } else if (key === "daily_profit") {
        expect(getDailyProfit(holding)).toBeCloseTo(value as number, 0);
      } else if (key === "holding_return_is_estimated") {
        expect(holding.holding_return_is_estimated).toBe(value);
      }
    }
  });
});
