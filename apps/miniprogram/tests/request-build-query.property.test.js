// Feature: miniprogram-web-parity, Property 7: 枚举请求参数构造
//
// Property 7 (design.md):
//   For any 合法枚举取值（range ∈ {today,week,month,year,all}、
//   lookback_days ∈ {3,5}、sort ∈ {change,streak,inflow}、calendar_year/month），
//   request.js 的 buildQuery(params) 生成的查询串包含且仅包含对应参数与值；
//   空字符串 / null / undefined 的参数被省略；非对象 / 空对象输入返回 ""。
//
// Validates: Requirements 9.2, 9.5, 10.3, 11.4
//
// 实现要点：
//   - 用 fast-check 生成「合法枚举值」与「空哨兵（''/null/undefined）」混合的参数对象。
//   - 断言查询串以 "?" 开头，且解析后键值集合恰等于「非空参数」集合（不多不少）。
//   - 断言每个被包含的值经 decodeURIComponent 后与原始值一致（值未被破坏）。
//   - 断言被省略的键不出现在查询串中。
//   - 断言非对象（数字/字符串/布尔/null/undefined/数组? 见下）与空对象返回 ""。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// request.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const { buildQuery } = require("../utils/request");

const NUM_RUNS = 200; // ≥ 100 次迭代

// 合法枚举取值生成器（与 design.md / requirements 9.2、9.5、10.3、11.4 对齐）。
const rangeArb = fc.constantFrom("today", "week", "month", "year", "all");
const lookbackArb = fc.constantFrom(3, 5);
const sortArb = fc.constantFrom("change", "streak", "inflow");
const calendarYearArb = fc.integer({ min: 2000, max: 2099 });
const calendarMonthArb = fc.integer({ min: 1, max: 12 });

// 「空哨兵」：被 buildQuery 省略的取值。
const emptyArb = fc.constantFrom("", null, undefined);

// 把可能合法、也可能为空的取值组合成一个 arbitrary。
function legalOrEmpty(legalArb) {
  return fc.oneof(legalArb, emptyArb);
}

// 解析 buildQuery 输出回 { key: value } 映射，便于「恰好包含」断言。
function parseQuery(qs) {
  if (qs === "") {
    return {};
  }
  expect(qs.startsWith("?")).toBe(true);
  const map = {};
  qs
    .slice(1)
    .split("&")
    .forEach((pair) => {
      const idx = pair.indexOf("=");
      const rawKey = idx >= 0 ? pair.slice(0, idx) : pair;
      const rawVal = idx >= 0 ? pair.slice(idx + 1) : "";
      map[decodeURIComponent(rawKey)] = decodeURIComponent(rawVal);
    });
  return map;
}

// 判定某取值是否应被省略（与 buildQuery 内部规则一致）。
function isOmitted(value) {
  return value === undefined || value === null || value === "";
}

describe("Property 7: 枚举请求参数构造", () => {
  it("查询串包含且仅包含非空枚举参数，且值未被破坏", () => {
    fc.assert(
      fc.property(
        fc.record({
          range: legalOrEmpty(rangeArb),
          lookback_days: legalOrEmpty(lookbackArb),
          sort: legalOrEmpty(sortArb),
          calendar_year: legalOrEmpty(calendarYearArb),
          calendar_month: legalOrEmpty(calendarMonthArb),
        }),
        (params) => {
          const qs = buildQuery(params);
          const parsed = parseQuery(qs);

          // 预期被包含的键 = 取值非空的键。
          const expectedKeys = Object.keys(params).filter(
            (k) => !isOmitted(params[k])
          );

          // 恰好包含：键集合相等（不多不少）。
          expect(Object.keys(parsed).sort()).toEqual(expectedKeys.sort());

          // 每个被包含的值经解码后与原始值字符串化一致（值未被破坏）。
          expectedKeys.forEach((k) => {
            expect(parsed[k]).toBe(String(params[k]));
          });

          // 被省略的键绝不出现在查询串中。
          Object.keys(params)
            .filter((k) => isOmitted(params[k]))
            .forEach((k) => {
              expect(qs.includes(encodeURIComponent(k) + "=")).toBe(false);
            });

          // 非空时以 "?" 开头；全部为空时为空串。
          if (expectedKeys.length === 0) {
            expect(qs).toBe("");
          } else {
            expect(qs.startsWith("?")).toBe(true);
          }
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("全部参数为空（''/null/undefined）或空对象时返回空串", () => {
    fc.assert(
      fc.property(
        fc.record({
          range: emptyArb,
          lookback_days: emptyArb,
          sort: emptyArb,
          calendar_year: emptyArb,
          calendar_month: emptyArb,
        }),
        (params) => {
          expect(buildQuery(params)).toBe("");
        }
      ),
      { numRuns: NUM_RUNS }
    );
    // 空对象同样返回空串。
    expect(buildQuery({})).toBe("");
  });

  it("非对象输入（数字/字符串/布尔/null/undefined）返回空串", () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.integer(),
          fc.string(),
          fc.boolean(),
          fc.constantFrom(null, undefined)
        ),
        (input) => {
          expect(buildQuery(input)).toBe("");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
