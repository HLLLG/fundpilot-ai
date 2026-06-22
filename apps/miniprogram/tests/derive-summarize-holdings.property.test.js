// Feature: miniprogram-web-parity, Property 17: 组合 KPI 汇总
//
// Property 17 (design.md):
//   For any 持仓列表，简报 KPI 的总资产等于各持仓 holding_amount 之和、
//   当日收益等于各持仓 daily_profit 之和（在浮点容差内），
//   空列表时各 KPI 为零而非异常。
//
// Validates: Requirements 17.2

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { summarizeHoldings } = require("../utils/derive");

const NUM_RUNS = 100; // ≥ 100 次迭代

// Floating-point tolerance for sum comparisons
const EPSILON = 1e-6;

// ---------------------------------------------------------------------------
// Arbitraries
// ---------------------------------------------------------------------------

/** 生成有限浮点数（不含 NaN / Infinity） */
const arbFiniteNumber = fc.double({
  min: -1e9,
  max: 1e9,
  noNaN: true,
  noDefaultInfinity: true,
});

/** 生成单个持仓对象，holding_amount 与 daily_profit 覆盖正常值、零、null、undefined */
const arbHolding = fc.record({
  fund_code: fc.string({ minLength: 1, maxLength: 10 }),
  fund_name: fc.string({ minLength: 1, maxLength: 20 }),
  holding_amount: fc.oneof(
    arbFiniteNumber,
    fc.constant(0),
    fc.constant(null),
    fc.constant(undefined)
  ),
  daily_profit: fc.oneof(
    arbFiniteNumber,
    fc.constant(0),
    fc.constant(null),
    fc.constant(undefined)
  ),
});

/** 生成任意规模持仓列表（0–30 条） */
const arbHoldings = fc.array(arbHolding, { minLength: 0, maxLength: 30 });

/** 生成含至少一条有效记录的持仓列表（用于验证非零汇总） */
const arbNonEmptyHoldings = fc.array(arbHolding, { minLength: 1, maxLength: 30 });

// ---------------------------------------------------------------------------
// Helper: 手工计算期望总和（跳过 null/undefined/非有限值）
// ---------------------------------------------------------------------------
function expectedSum(holdings, field) {
  let total = 0;
  for (const h of holdings) {
    if (h == null) continue;
    const v = Number(h[field]);
    if (Number.isFinite(v)) total += v;
  }
  return total;
}

// ---------------------------------------------------------------------------
// Property 17
// ---------------------------------------------------------------------------

describe("Property 17: 组合 KPI 汇总", () => {
  it(
    "totalAssets 等于各持仓 holding_amount 有限值之和（浮点容差内）",
    () => {
      fc.assert(
        fc.property(arbNonEmptyHoldings, (holdings) => {
          const { totalAssets } = summarizeHoldings(holdings);
          const expected = expectedSum(holdings, "holding_amount");
          expect(Math.abs(totalAssets - expected)).toBeLessThanOrEqual(
            EPSILON * (Math.abs(expected) + 1)
          );
        }),
        { numRuns: NUM_RUNS }
      );
    }
  );

  it(
    "dailyProfit 等于各持仓 daily_profit 有限值之和（浮点容差内）",
    () => {
      fc.assert(
        fc.property(arbNonEmptyHoldings, (holdings) => {
          const { dailyProfit } = summarizeHoldings(holdings);
          const expected = expectedSum(holdings, "daily_profit");
          expect(Math.abs(dailyProfit - expected)).toBeLessThanOrEqual(
            EPSILON * (Math.abs(expected) + 1)
          );
        }),
        { numRuns: NUM_RUNS }
      );
    }
  );

  it(
    "空列表时 totalAssets 与 dailyProfit 均为 0，不抛错",
    () => {
      fc.assert(
        fc.property(fc.constant([]), () => {
          let result;
          expect(() => {
            result = summarizeHoldings([]);
          }).not.toThrow();
          expect(result.totalAssets).toBe(0);
          expect(result.dailyProfit).toBe(0);
        }),
        { numRuns: NUM_RUNS }
      );
    }
  );

  it(
    "null/undefined/非有限值 holding_amount 与 daily_profit 不计入汇总",
    () => {
      fc.assert(
        fc.property(arbHoldings, (holdings) => {
          const { totalAssets, dailyProfit } = summarizeHoldings(holdings);
          // Both sums must be finite numbers (not NaN, not Infinity)
          expect(Number.isFinite(totalAssets)).toBe(true);
          expect(Number.isFinite(dailyProfit)).toBe(true);
        }),
        { numRuns: NUM_RUNS }
      );
    }
  );

  it(
    "任意持仓列表（含边界值）调用不抛错，返回对象含 totalAssets 与 dailyProfit 字段",
    () => {
      fc.assert(
        fc.property(arbHoldings, (holdings) => {
          let result;
          expect(() => {
            result = summarizeHoldings(holdings);
          }).not.toThrow();
          expect(result).toHaveProperty("totalAssets");
          expect(result).toHaveProperty("dailyProfit");
          expect(typeof result.totalAssets).toBe("number");
          expect(typeof result.dailyProfit).toBe("number");
        }),
        { numRuns: NUM_RUNS }
      );
    }
  );
});
