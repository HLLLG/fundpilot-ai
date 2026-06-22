// Feature: miniprogram-web-parity, Property 10: 盈亏日历配色
//
// Property 10 (design.md):
//   For any 盈亏日历的 days[]，每个交易日的着色类与其 daily_profit 符号一致
//   （正→'up'、负→'down'、零→'neutral'），非交易日渲染为占位（'placeholder'）
//   且不参与盈亏配色。
//
// Validates: Requirements 9.4
//
// 实现要点：
//   - 用 fast-check 生成任意 days 数组，每项随机决定是否为交易日，
//     以及 daily_profit 的符号（正/负/零/NaN/null）。
//   - 断言交易日的 colorClass 与 daily_profit 符号严格一致。
//   - 断言非交易日的 colorClass 一律为 'placeholder'。
//   - 断言输入不被修改（纯函数性）。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// derive.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const { mapCalendarColors } = require("../utils/derive");

const NUM_RUNS = 200; // ≥ 100 次迭代

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** 生成 daily_profit 的各种情形：有限正数、有限负数、零、null、undefined、NaN */
const dailyProfitArb = fc.oneof(
  // 有限正数（排除零）
  fc.double({ min: 1e-10, max: 1e12, noNaN: true, noDefaultInfinity: true }),
  // 有限负数（排除零）
  fc.double({ min: -1e12, max: -1e-10, noNaN: true, noDefaultInfinity: true }),
  // 零（精确）
  fc.constant(0),
  // null / undefined（缺失值）
  fc.constant(null),
  fc.constant(undefined),
  // NaN
  fc.constant(NaN)
);

/** 生成单个日历项 */
const calendarDayArb = fc.record({
  is_trading_day: fc.boolean(),
  daily_profit: dailyProfitArb,
  // 其他字段（日历格中可能含有的任意元数据，确认 mapCalendarColors 不破坏它们）
  date: fc.option(fc.string({ minLength: 1, maxLength: 10 }), { nil: undefined }),
});

/** 生成日历 days 数组（0..31 项，覆盖空数组与正常月份） */
const daysArrayArb = fc.array(calendarDayArb, { minLength: 0, maxLength: 31 });

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * 根据 daily_profit 判断预期的 colorClass（交易日场景）。
 * 与 mapCalendarColors 实现逻辑对称，用于属性断言。
 */
function expectedColorClass(isTrading, dailyProfit) {
  if (!isTrading) return "placeholder";
  const dp = Number(dailyProfit);
  if (!Number.isFinite(dp) || dp === 0) return "neutral";
  if (dp > 0) return "up";
  return "down";
}

// ---------------------------------------------------------------------------
// Properties
// ---------------------------------------------------------------------------

describe("Property 10: 盈亏日历配色", () => {
  it("交易日 colorClass 与 daily_profit 符号一致，非交易日为 placeholder", () => {
    fc.assert(
      fc.property(daysArrayArb, (days) => {
        // 保存输入快照，用于验证纯函数性。
        const inputSnapshot = JSON.stringify(days);

        const result = mapCalendarColors(days);

        // 输出数组长度与输入一致。
        expect(result).toHaveLength(days.length);

        for (let i = 0; i < days.length; i++) {
          const input = days[i];
          const output = result[i];

          if (input == null) {
            // null 输入项原样映射为 null（函数处理 null 项返回 null）。
            expect(output).toBeNull();
            continue;
          }

          // colorClass 必须存在且为字符串。
          expect(typeof output.colorClass).toBe("string");

          // colorClass 与 is_trading_day 和 daily_profit 的符号严格一致。
          const expected = expectedColorClass(
            input.is_trading_day,
            input.daily_profit
          );
          expect(output.colorClass).toBe(expected);

          // 非交易日：colorClass 一律为 'placeholder'，不受 daily_profit 影响。
          if (!input.is_trading_day) {
            expect(output.colorClass).toBe("placeholder");
          }

          // 交易日：colorClass 只能是 'up' / 'down' / 'neutral'（三值完备）。
          if (input.is_trading_day) {
            expect(["up", "down", "neutral"]).toContain(output.colorClass);
          }

          // 正盈利 → 'up'
          if (
            input.is_trading_day &&
            Number.isFinite(Number(input.daily_profit)) &&
            Number(input.daily_profit) > 0
          ) {
            expect(output.colorClass).toBe("up");
          }

          // 负盈亏 → 'down'
          if (
            input.is_trading_day &&
            Number.isFinite(Number(input.daily_profit)) &&
            Number(input.daily_profit) < 0
          ) {
            expect(output.colorClass).toBe("down");
          }

          // 零或非有限值 → 'neutral'
          if (
            input.is_trading_day &&
            (!Number.isFinite(Number(input.daily_profit)) ||
              Number(input.daily_profit) === 0)
          ) {
            expect(output.colorClass).toBe("neutral");
          }
        }

        // 纯函数性：输入数组不被修改。
        expect(JSON.stringify(days)).toBe(inputSnapshot);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("非数组输入返回空数组（防御性）", () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.constant(null),
          fc.constant(undefined),
          fc.integer(),
          fc.string()
        ),
        (nonArray) => {
          const result = mapCalendarColors(nonArray);
          expect(Array.isArray(result)).toBe(true);
          expect(result).toHaveLength(0);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("返回的每个项是输入项的浅拷贝，不共享引用（纯函数性）", () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            is_trading_day: fc.boolean(),
            daily_profit: fc.double({
              min: -1e6,
              max: 1e6,
              noNaN: true,
              noDefaultInfinity: true,
            }),
          }),
          { minLength: 1, maxLength: 10 }
        ),
        (days) => {
          const result = mapCalendarColors(days);
          for (let i = 0; i < days.length; i++) {
            // 输出项不是输入项同一引用（已拷贝）。
            expect(result[i]).not.toBe(days[i]);
            // 输出项保留原始字段值（如 is_trading_day、daily_profit）。
            expect(result[i].is_trading_day).toBe(days[i].is_trading_day);
            expect(result[i].daily_profit).toBe(days[i].daily_profit);
          }
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
