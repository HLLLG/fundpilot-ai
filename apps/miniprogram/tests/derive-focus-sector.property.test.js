// Feature: miniprogram-web-parity, Property 12: 关注方向增减约束
//
// Property 12 (design.md):
//   For any 已有关注方向集合与新加入方向，加入操作后的集合去重、长度 ≤ 3；
//   当集合已满（3 个）且新方向不在其中时集合保持不变。
//
// Validates: Requirements 10.7
//
// 实现要点：
//   - 用 fast-check 生成任意字符串数组（当前集合）与任意字符串（新板块）。
//   - 断言结果去重（无重复元素）、长度 ≤ 3。
//   - 断言当前集合已满（length === 3）且新板块不在其中时，结果与原集合深度相等。
//   - 断言原数组未被修改（immutability）。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// derive.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const derive = require("../utils/derive");

const { addFocusSector } = derive;

const NUM_RUNS = 200; // ≥ 100 次迭代

// 辅助：判断数组是否无重复元素。
function isDeduped(arr) {
  return arr.length === new Set(arr).size;
}

// 辅助：非空字符串任意值生成器，避免空字符串（addFocusSector 对空字符串作为无效输入不加入）。
const sectorArb = fc.string({ minLength: 1, maxLength: 20 });

// 辅助：生成已去重且长度 0-3 的字符串数组（模拟合法的当前关注方向集合）。
const currentArb = fc
  .uniqueArray(sectorArb, { minLength: 0, maxLength: 3 });

describe("Property 12: 关注方向增减约束", () => {
  it("加入后结果去重且长度 ≤ 3", () => {
    fc.assert(
      fc.property(currentArb, sectorArb, (current, newSector) => {
        const result = addFocusSector(current, newSector);

        // 结果必须去重。
        expect(isDeduped(result)).toBe(true);

        // 结果长度 ≤ 3。
        expect(result.length).toBeLessThanOrEqual(3);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("集合已满（length === 3）且新方向不在其中时，结果保持不变", () => {
    fc.assert(
      fc.property(
        // 生成恰好 3 个不同元素的当前集合。
        fc.uniqueArray(sectorArb, { minLength: 3, maxLength: 3 }),
        sectorArb,
        (current, newSector) => {
          // 前置条件：新板块不在当前集合中。
          fc.pre(!current.includes(newSector));

          const result = addFocusSector(current, newSector);

          // 集合已满且新板块不在其中 → 结果应与原集合深度相等（保持不变）。
          expect(result).toStrictEqual(current);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("原数组未被修改（immutability）", () => {
    fc.assert(
      fc.property(currentArb, sectorArb, (current, newSector) => {
        const snapshot = current.slice();
        addFocusSector(current, newSector);

        // 调用后原数组与调用前的快照深度相等。
        expect(current).toStrictEqual(snapshot);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("新方向已在集合中时，结果与原集合深度相等（不重复加入）", () => {
    fc.assert(
      fc.property(
        fc.uniqueArray(sectorArb, { minLength: 1, maxLength: 3 }),
        (current) => {
          // 随机取一个已有的板块作为"新方向"。
          const existing = current[0];
          const result = addFocusSector(current, existing);

          expect(result).toStrictEqual(current);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("未满时加入新方向后，新方向出现在结果中且长度增加 1", () => {
    fc.assert(
      fc.property(
        fc.uniqueArray(sectorArb, { minLength: 0, maxLength: 2 }),
        sectorArb,
        (current, newSector) => {
          // 前置条件：新板块不在当前集合中（确保会被加入）。
          fc.pre(!current.includes(newSector));

          const result = addFocusSector(current, newSector);

          // 结果应包含新板块。
          expect(result).toContain(newSector);
          // 长度增加 1。
          expect(result.length).toBe(current.length + 1);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
