// Feature: miniprogram-web-parity, Property 11: 当日 TOP5 推导
//
// Property 11 (design.md):
//   For any 含 daily_profit 的基金列表，推导出的盈利 TOP5 全为正且按降序、
//   数量 ≤ 5，亏损 TOP5 全为负且按升序、数量 ≤ 5，且两个列表不含同一基金。
//
// Validates: Requirements 9.6

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { deriveDailyTop5 } = require("../utils/derive");

const NUM_RUNS = 100; // ≥ 100 次迭代

// ---------------------------------------------------------------------------
// Arbitraries
// ---------------------------------------------------------------------------

/** 生成单个基金对象，daily_profit 可为正、负、零、null 或非数字 */
const arbHolding = fc.record({
  fund_code: fc.string({ minLength: 1, maxLength: 10 }),
  fund_name: fc.string({ minLength: 1, maxLength: 20 }),
  daily_profit: fc.oneof(
    fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true }),
    fc.constant(0),
    fc.constant(null),
    fc.constant(undefined)
  ),
});

/** 生成任意规模的持仓列表（0–30 条）*/
const arbHoldings = fc.array(arbHolding, { minLength: 0, maxLength: 30 });

// ---------------------------------------------------------------------------
// Property 11
// ---------------------------------------------------------------------------

describe("Property 11: 当日 TOP5 推导", () => {
  it(
    "盈利 TOP5 全为正值、降序排列、数量 ≤ 5；亏损 TOP5 全为负值、升序排列、数量 ≤ 5；两列表互斥",
    () => {
      fc.assert(
        fc.property(arbHoldings, (holdings) => {
          const { gainers, losers } = deriveDailyTop5(holdings);

          // ── 数量约束 ──────────────────────────────────────────────────────
          expect(gainers.length).toBeLessThanOrEqual(5);
          expect(losers.length).toBeLessThanOrEqual(5);

          // ── 盈利列表：全部 daily_profit > 0 ──────────────────────────────
          for (const g of gainers) {
            const dp = Number(g.daily_profit);
            expect(Number.isFinite(dp)).toBe(true);
            expect(dp).toBeGreaterThan(0);
          }

          // ── 亏损列表：全部 daily_profit < 0 ──────────────────────────────
          for (const l of losers) {
            const dp = Number(l.daily_profit);
            expect(Number.isFinite(dp)).toBe(true);
            expect(dp).toBeLessThan(0);
          }

          // ── 盈利列表降序（后一项 ≤ 前一项）──────────────────────────────
          for (let i = 0; i < gainers.length - 1; i++) {
            expect(Number(gainers[i].daily_profit)).toBeGreaterThanOrEqual(
              Number(gainers[i + 1].daily_profit)
            );
          }

          // ── 亏损列表升序（后一项 ≥ 前一项，即绝对值最大的在最前面）──────
          for (let i = 0; i < losers.length - 1; i++) {
            expect(Number(losers[i].daily_profit)).toBeLessThanOrEqual(
              Number(losers[i + 1].daily_profit)
            );
          }

          // ── 两个列表互斥（不含同一对象引用）────────────────────────────
          const gainerSet = new Set(gainers);
          for (const l of losers) {
            expect(gainerSet.has(l)).toBe(false);
          }
        }),
        { numRuns: NUM_RUNS }
      );
    }
  );

  it("空列表或全零/null profit 时返回空 gainers 与 losers，不抛错", () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            fund_code: fc.string({ minLength: 1, maxLength: 6 }),
            daily_profit: fc.oneof(fc.constant(0), fc.constant(null), fc.constant(undefined)),
          }),
          { minLength: 0, maxLength: 10 }
        ),
        (holdings) => {
          const { gainers, losers } = deriveDailyTop5(holdings);
          expect(gainers.length).toBe(0);
          expect(losers.length).toBe(0);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("盈利/亏损条目不超过来源列表中实际正/负数量", () => {
    fc.assert(
      fc.property(arbHoldings, (holdings) => {
        const positiveCount = holdings.filter((h) => {
          if (h == null) return false;
          const dp = Number(h.daily_profit);
          return Number.isFinite(dp) && dp > 0;
        }).length;
        const negativeCount = holdings.filter((h) => {
          if (h == null) return false;
          const dp = Number(h.daily_profit);
          return Number.isFinite(dp) && dp < 0;
        }).length;

        const { gainers, losers } = deriveDailyTop5(holdings);

        expect(gainers.length).toBeLessThanOrEqual(Math.min(5, positiveCount));
        expect(losers.length).toBeLessThanOrEqual(Math.min(5, negativeCount));
      }),
      { numRuns: NUM_RUNS }
    );
  });
});
