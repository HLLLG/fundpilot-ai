// Feature: miniprogram-web-parity, Property 13: 美股数据源不可用占位
//
// Property 13 (design.md):
//   For any 行情条目 { status, last_price, change_percent }，
//   当 status === "unavailable" 时展示映射输出占位且不渲染任何数值
//   （isUnavailable: true, last_price: null, change_percent: null）；
//   当 status ∈ {ok, stale} 时渲染对应数值字段。
//
// Validates: Requirements 12.6

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// derive.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const { mapUsQuoteDisplay } = require("../utils/derive");

const NUM_RUNS = 200; // ≥ 100 次迭代

// ---------------------------------------------------------------------------
// Arbitraries
// ---------------------------------------------------------------------------

/** 任意有限数值或 null（模拟 last_price / change_percent 字段）*/
const arbNumericField = fc.oneof(
  fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true }),
  fc.constant(null),
  fc.constant(undefined)
);

/** 状态为 'unavailable' 的行情条目 */
const arbUnavailableItem = fc.record({
  status: fc.constant("unavailable"),
  last_price: arbNumericField,
  change_percent: arbNumericField,
});

/** 状态为 'ok' 或 'stale' 的行情条目，数值字段为有限数字 */
const arbAvailableItem = fc.record({
  status: fc.oneof(fc.constant("ok"), fc.constant("stale")),
  last_price: fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true }),
  change_percent: fc.double({ min: -100, max: 100, noNaN: true, noDefaultInfinity: true }),
});

/** 任意状态（包含非预期值）的行情条目 */
const arbAnyItem = fc.oneof(
  arbUnavailableItem,
  arbAvailableItem,
  fc.record({
    status: fc.string({ minLength: 0, maxLength: 20 }),
    last_price: arbNumericField,
    change_percent: arbNumericField,
  })
);

// ---------------------------------------------------------------------------
// Property 13: status === 'unavailable' → 占位（null 值，isUnavailable: true）
// ---------------------------------------------------------------------------

describe("Property 13: 美股数据源不可用占位", () => {
  it("status === 'unavailable' 时，last_price 和 change_percent 均为 null，isUnavailable 为 true", () => {
    fc.assert(
      fc.property(arbUnavailableItem, (item) => {
        const result = mapUsQuoteDisplay(item);

        // 占位标志必须为 true
        expect(result.isUnavailable).toBe(true);

        // 数值字段不得渲染任何值——必须为 null
        expect(result.last_price).toBeNull();
        expect(result.change_percent).toBeNull();

        // status 字段透传
        expect(result.status).toBe("unavailable");
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // ---------------------------------------------------------------------------
  // Property 13: status ∈ {ok, stale} → 渲染原数值字段
  // ---------------------------------------------------------------------------

  it("status 为 'ok' 时，last_price 和 change_percent 渲染原值，isUnavailable 为 false", () => {
    fc.assert(
      fc.property(
        fc.record({
          status: fc.constant("ok"),
          last_price: fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true }),
          change_percent: fc.double({ min: -100, max: 100, noNaN: true, noDefaultInfinity: true }),
        }),
        (item) => {
          const result = mapUsQuoteDisplay(item);

          // 非占位
          expect(result.isUnavailable).toBe(false);

          // 数值字段与输入一致
          expect(result.last_price).toBe(item.last_price);
          expect(result.change_percent).toBe(item.change_percent);

          // status 透传
          expect(result.status).toBe("ok");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("status 为 'stale' 时，last_price 和 change_percent 渲染原值，isUnavailable 为 false", () => {
    fc.assert(
      fc.property(
        fc.record({
          status: fc.constant("stale"),
          last_price: fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true }),
          change_percent: fc.double({ min: -100, max: 100, noNaN: true, noDefaultInfinity: true }),
        }),
        (item) => {
          const result = mapUsQuoteDisplay(item);

          // 非占位
          expect(result.isUnavailable).toBe(false);

          // 数值字段与输入一致
          expect(result.last_price).toBe(item.last_price);
          expect(result.change_percent).toBe(item.change_percent);

          // status 透传
          expect(result.status).toBe("stale");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // ---------------------------------------------------------------------------
  // 跨所有输入：返回对象始终包含必要字段且不抛错
  // ---------------------------------------------------------------------------

  it("对任意输入（含 null/undefined 条目），函数不抛出异常且返回结构完整", () => {
    fc.assert(
      fc.property(
        fc.oneof(arbAnyItem, fc.constant(null), fc.constant(undefined)),
        (item) => {
          let result;
          expect(() => {
            result = mapUsQuoteDisplay(item);
          }).not.toThrow();

          // 返回对象必须包含所有预期字段
          expect(result).toHaveProperty("isUnavailable");
          expect(result).toHaveProperty("last_price");
          expect(result).toHaveProperty("change_percent");
          expect(result).toHaveProperty("status");

          // isUnavailable 必须为布尔类型
          expect(typeof result.isUnavailable).toBe("boolean");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // ---------------------------------------------------------------------------
  // 当 isUnavailable 为 true 时，last_price 和 change_percent 必须为 null（跨所有输入验证）
  // ---------------------------------------------------------------------------

  it("对任意输入，isUnavailable === true 时数值字段不含任何渲染值（均为 null）", () => {
    fc.assert(
      fc.property(arbAnyItem, (item) => {
        const result = mapUsQuoteDisplay(item);

        if (result.isUnavailable) {
          // 占位状态下，数值字段均须为 null
          expect(result.last_price).toBeNull();
          expect(result.change_percent).toBeNull();
        }
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // ---------------------------------------------------------------------------
  // 当 isUnavailable 为 false 时，数值字段与输入一致（避免字段丢失）
  // ---------------------------------------------------------------------------

  it("status 为 'ok' 或 'stale' 时，isUnavailable 始终为 false", () => {
    fc.assert(
      fc.property(arbAvailableItem, (item) => {
        const result = mapUsQuoteDisplay(item);

        // ok / stale 状态下必须非占位
        expect(result.isUnavailable).toBe(false);
      }),
      { numRuns: NUM_RUNS }
    );
  });
});
