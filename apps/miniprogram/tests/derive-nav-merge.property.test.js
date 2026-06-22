// Feature: miniprogram-web-parity, Property 9: 净值分页合并
//
// Property 9 (design.md):
//   For any 由 nav-history/page 游标分页返回的若干页净值点序列，合并后的列表
//   按日期有序、无重复日期，且长度等于各页去重后并集大小。
//
// Validates: Requirements 8.4
//
// 实现要点：
//   - 用 fc.uniqueArray 生成每页内不重复日期的净值点数组（页内无重复）。
//   - 用 fc.array 生成若干页（模拟游标分页）。
//   - 断言合并结果按日期严格升序排列（date-ordered）。
//   - 断言合并结果无重复日期（no duplicate dates）。
//   - 断言合并结果长度等于所有页中出现过的不重复日期的总数（union dedup size）。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// derive.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const { mergeNavPages } = require("../utils/derive");

const NUM_RUNS = 200; // ≥ 100 次迭代

// ---------------------------------------------------------------------------
// 辅助生成器
// ---------------------------------------------------------------------------

/**
 * 生成一个格式为 "YYYY-MM-DD" 的随机日期字符串。
 * 范围：2015-01-01 ~ 2030-12-31，保证格式正规、可字符串比较排序。
 */
const dateString = fc
  .integer({ min: 2015, max: 2030 })
  .chain((year) =>
    fc.integer({ min: 1, max: 12 }).chain((month) =>
      fc.integer({ min: 1, max: 28 }).map((day) => {
        const mm = String(month).padStart(2, "0");
        const dd = String(day).padStart(2, "0");
        return `${year}-${mm}-${dd}`;
      })
    )
  );

/**
 * 单页净值点：date 唯一（页内无重复）并附带任意净值数据。
 * 使用 fc.uniqueArray 确保页内日期不重复。
 */
const navPage = fc.uniqueArray(
  fc.record({
    date: dateString,
    nav: fc.float({ min: 0.5, max: 5.0, noNaN: true, noDefaultInfinity: true }),
    acc_nav: fc.float({ min: 0.5, max: 10.0, noNaN: true, noDefaultInfinity: true }),
  }),
  {
    selector: (item) => item.date,
    minLength: 0,
    maxLength: 30,
  }
);

/**
 * 若干页的数组（模拟游标分页），每页内日期唯一，跨页允许重叠（去重逻辑由 mergeNavPages 负责）。
 */
const navPages = fc.array(navPage, { minLength: 0, maxLength: 10 });

// ---------------------------------------------------------------------------
// 测试
// ---------------------------------------------------------------------------

describe("Property 9: 净值分页合并", () => {
  it("合并结果按日期严格升序排列（date-ordered）", () => {
    fc.assert(
      fc.property(navPages, (pages) => {
        const merged = mergeNavPages(pages);

        for (let i = 1; i < merged.length; i++) {
          // 相邻元素日期必须严格递增（已排序且无重复）。
          expect(merged[i].date > merged[i - 1].date).toBe(true);
        }
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("合并结果无重复日期（no duplicate dates）", () => {
    fc.assert(
      fc.property(navPages, (pages) => {
        const merged = mergeNavPages(pages);
        const dates = merged.map((item) => item.date);
        const uniqueDates = new Set(dates);

        expect(uniqueDates.size).toBe(dates.length);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("合并长度等于所有页去重后并集大小（union dedup size）", () => {
    fc.assert(
      fc.property(navPages, (pages) => {
        const merged = mergeNavPages(pages);

        // 计算所有页中出现过的不重复日期总数（reference implementation）。
        const allDates = new Set();
        for (const page of pages) {
          if (!Array.isArray(page)) continue;
          for (const item of page) {
            if (item && item.date) allDates.add(item.date);
          }
        }

        expect(merged.length).toBe(allDates.size);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("空输入与边界：null/undefined/空数组 不抛错，返回空数组", () => {
    fc.assert(
      fc.property(fc.constant(null), (_) => {
        expect(() => mergeNavPages(null)).not.toThrow();
        expect(mergeNavPages(null)).toStrictEqual([]);
        expect(() => mergeNavPages([])).not.toThrow();
        expect(mergeNavPages([])).toStrictEqual([]);
        expect(() => mergeNavPages([[], []])).not.toThrow();
        expect(mergeNavPages([[], []])).toStrictEqual([]);
      }),
      { numRuns: 1 }
    );
  });

  it("跨页重复日期只保留首次出现的条目（first-wins dedup）", () => {
    fc.assert(
      fc.property(
        // 两页共享至少一个相同日期。
        fc.uniqueArray(dateString, { minLength: 2, maxLength: 20 }).chain(
          (dates) => {
            // 随机选取一个重叠日期集合（非空子集）。
            const overlapSize = Math.max(1, Math.floor(dates.length / 3));
            const overlapDates = dates.slice(0, overlapSize);
            const restDates = dates.slice(overlapSize);

            // 页 A：overlapDates + 前半 restDates
            const halfRest = Math.floor(restDates.length / 2);
            const pageDatesA = [...overlapDates, ...restDates.slice(0, halfRest)];
            const pageDatesB = [...overlapDates, ...restDates.slice(halfRest)];

            const pageA = pageDatesA.map((d) => ({
              date: d,
              nav: 1.0,
              source: "A",
            }));
            const pageB = pageDatesB.map((d) => ({
              date: d,
              nav: 2.0,
              source: "B",
            }));

            return fc.constant([pageA, pageB]);
          }
        ),
        (pages) => {
          const merged = mergeNavPages(pages);
          const dates = merged.map((item) => item.date);
          const uniqueDates = new Set(dates);

          // 无重复日期
          expect(uniqueDates.size).toBe(dates.length);

          // 重叠日期由页 A（首页）获胜（source === 'A'）
          const pageA = pages[0];
          const overlapDatesFromA = new Set(pageA.map((i) => i.date));
          for (const item of merged) {
            if (overlapDatesFromA.has(item.date) && pages[1].some((i) => i.date === item.date)) {
              // 该日期同时存在于两页 → 应取页 A 的条目（source 'A'）
              expect(item.source).toBe("A");
            }
          }
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
