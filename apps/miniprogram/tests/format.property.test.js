// Feature: miniprogram-web-parity, Property 1: 数字格式化的符号与配色一致性
//
// Property 1 (design.md):
//   For any 数值 v，format.js 的数字格式化对正值着「涨」色并加「+」前缀、
//   对负值着「跌」色、对零取中性，且千分位与百分号格式不改变其数值含义。
//
// Validates: Requirements 1.6, 5.3, 10.2
//
// 实现要点：
//   - 用 fast-check 生成任意有限数值（含整数/小数/极值/字符串数字）。
//   - 断言「符号 ↔ 配色（tone/toneClass）↔「+」前缀」三者一致。
//   - 断言千分位与百分号只改变展示形态，不改变数值含义（去格式化后数值相等）。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// format.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const fmt = require("../utils/format");

const NUM_RUNS = 200; // ≥ 100 次迭代

// 把已格式化文本还原为数值：去掉前缀符号、千分位逗号、百分号后用 Number 解析。
function unformat(text) {
  const cleaned = text.replace(/,/g, "").replace(/%$/, "");
  return Number(cleaned);
}

describe("Property 1: 数字格式化的符号与配色一致性", () => {
  it("符号与配色（tone / toneClass / 「+」前缀）在所有数值上保持一致", () => {
    fc.assert(
      fc.property(
        // 覆盖正/负/零与各种量级的有限浮点。
        fc.double({ min: -1e12, max: 1e12, noNaN: true, noDefaultInfinity: true }),
        // 展示精度 0..6 位。
        fc.integer({ min: 0, max: 6 }),
        (v, digits) => {
          const rounded = fmt.roundTo(v, digits);
          const tone = fmt.tone(v, digits);
          const toneClass = fmt.toneClass(v, { digits: digits });

          // 配色 token 与四舍五入后的符号严格一致。
          if (rounded > 0) {
            expect(tone).toBe(fmt.TONE_UP);
          } else if (rounded < 0) {
            expect(tone).toBe(fmt.TONE_DOWN);
          } else {
            expect(tone).toBe(fmt.TONE_FLAT);
          }
          // toneClass 必须由 tone 派生。
          expect(toneClass).toBe("num-" + tone);

          // 带符号格式化：仅正值（涨）加「+」前缀；负值带「-」；零/中性无「+」。
          const signed = fmt.format(v, { digits: digits, signed: true, grouping: false });
          if (tone === fmt.TONE_UP) {
            expect(signed.startsWith("+")).toBe(true);
          } else {
            expect(signed.startsWith("+")).toBe(false);
          }
          if (tone === fmt.TONE_DOWN) {
            expect(signed.startsWith("-")).toBe(true);
          }
          if (tone === fmt.TONE_FLAT) {
            // 中性值不应带任何正负号前缀。
            expect(signed.startsWith("+")).toBe(false);
            expect(signed.startsWith("-")).toBe(false);
          }
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("千分位与百分号只改变展示形态，不改变数值含义", () => {
    fc.assert(
      fc.property(
        fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true }),
        fc.integer({ min: 0, max: 4 }),
        (v, digits) => {
          const rounded = fmt.roundTo(v, digits);

          // 千分位 vs 无千分位：去掉逗号后数值相等。
          const grouped = fmt.format(v, { digits: digits, grouping: true });
          const plain = fmt.format(v, { digits: digits, grouping: false });
          expect(unformat(grouped)).toBeCloseTo(unformat(plain), 6);
          // 去掉千分位逗号后应与无分组形态一致（纯展示差异）。
          expect(grouped.replace(/,/g, "")).toBe(plain);

          // 还原后的数值等于按精度四舍五入后的值（格式化未改变数值含义）。
          expect(unformat(grouped)).toBeCloseTo(rounded, 6);

          // 百分号：formatPercent 末尾为「%」，去掉后数值与同口径数字一致。
          const pct = fmt.formatPercent(v, { digits: digits, grouping: false });
          expect(pct.endsWith("%")).toBe(true);
          const pctNumeric = fmt.format(v, { digits: digits, signed: true, grouping: false });
          expect(pct).toBe(pctNumeric + "%");
          expect(unformat(pct)).toBeCloseTo(rounded, 6);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
