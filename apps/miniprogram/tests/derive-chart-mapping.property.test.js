// Feature: miniprogram-web-parity, Property 8: 图表数据映射保真
//
// Property 8 (design.md):
//   For any 时间序列点集与持仓分布数据，derive.js 生成的图表 option 满足：
//   (1) 折线 series 的数据点数等于输入点数且 x 轴时间严格有序不丢点；
//   (2) 甜甜圈各扇区值等于各持仓 weight_percent 且其总和在容差内等于 100%。
//
// Validates: Requirements 8.2, 9.3, 9.7

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const derive = require("../utils/derive");

const NUM_RUNS = 100; // ≥ 100 次迭代（约定）

// ---------------------------------------------------------------------------
// 辅助：生成合法的时间/日期字符串（ISO 日期 YYYY-MM-DD 或 HH:MM）
// ---------------------------------------------------------------------------
const arbDate = fc
  .tuple(
    fc.integer({ min: 2000, max: 2030 }),
    fc.integer({ min: 1, max: 12 }),
    fc.integer({ min: 1, max: 28 }),
  )
  .map(([y, m, d]) => {
    const mm = String(m).padStart(2, "0");
    const dd = String(d).padStart(2, "0");
    return `${y}-${mm}-${dd}`;
  });

// 点集生成器：每个点有唯一的日期标签（用索引保证唯一）
const arbLinePoints = (len) =>
  fc.array(
    fc.record({
      portfolio_percent: fc.double({ min: -50, max: 200, noNaN: true, noDefaultInfinity: true }),
      index_percent: fc.double({ min: -50, max: 200, noNaN: true, noDefaultInfinity: true }),
    }),
    { minLength: len, maxLength: len }
  ).map((items) =>
    items.map((item, i) => ({
      ...item,
      // 用索引构造严格唯一的日期，便于验证顺序
      date: `2024-01-${String(i + 1).padStart(2, "0")}`,
    }))
  );

// 乱序辅助：Fisher-Yates shuffle（纯函数，不修改原数组）
function shuffleArray(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// ---------------------------------------------------------------------------
// Property 8-Line: 折线图数据点数等于输入点数且 x 轴时间严格有序
// ---------------------------------------------------------------------------
describe("Property 8: 图表数据映射保真 — buildLineChartOption", () => {
  it("折线 series 数据点数等于输入点数", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 50 }).chain((len) => arbLinePoints(len)),
        (points) => {
          const option = derive.buildLineChartOption(points);

          // x 轴数据点数必须等于输入点数
          expect(option.xAxis.data.length).toBe(points.length);

          // 两条 series（我的收益 + 沪深300）各自的数据点数也必须等于输入点数
          expect(option.series[0].data.length).toBe(points.length);
          expect(option.series[1].data.length).toBe(points.length);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("x 轴时间标签严格有序（不丢点，顺序与输入无关）", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 2, max: 30 }).chain((len) => arbLinePoints(len)),
        (points) => {
          // 打乱顺序，验证 buildLineChartOption 始终自行排序
          const shuffled = shuffleArray(points);
          const option = derive.buildLineChartOption(shuffled);

          const xData = option.xAxis.data;

          // x 轴时间严格有序（非降序）
          for (let i = 1; i < xData.length; i++) {
            expect(xData[i] >= xData[i - 1]).toBe(true);
          }

          // 不丢点：x 轴数据个数等于输入个数
          expect(xData.length).toBe(points.length);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("空输入返回空 series 且不抛错", () => {
    const option = derive.buildLineChartOption([]);
    expect(option.xAxis.data.length).toBe(0);
    expect(option.series[0].data.length).toBe(0);
    expect(option.series[1].data.length).toBe(0);
  });

  it("非数组输入不抛错", () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.constant(null),
          fc.constant(undefined),
          fc.string(),
          fc.integer()
        ),
        (input) => {
          expect(() => derive.buildLineChartOption(input)).not.toThrow();
          const option = derive.buildLineChartOption(input);
          expect(option.series[0].data.length).toBe(0);
          expect(option.series[1].data.length).toBe(0);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});

// ---------------------------------------------------------------------------
// Property 8-Donut: 甜甜圈各扇区值等于 weight_percent，总和≈100%
// ---------------------------------------------------------------------------

// 生成持仓分布数组（weight_percent 之和精确为 100 的 N 条分配）
function arbAllocation(n) {
  // 生成 n 个随机正数，然后归一化到总和=100
  return fc
    .array(fc.double({ min: 0.01, max: 100, noNaN: true, noDefaultInfinity: true }), {
      minLength: n,
      maxLength: n,
    })
    .map((weights) => {
      const total = weights.reduce((s, w) => s + w, 0);
      return weights.map((w, i) => ({
        fund_code: `F${String(i).padStart(6, "0")}`,
        fund_name: `基金${i}`,
        weight_percent: (w / total) * 100,
      }));
    });
}

describe("Property 8: 图表数据映射保真 — buildDonutChartOption", () => {
  it("各扇区 value 等于对应 weight_percent", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 20 }).chain((n) => arbAllocation(n)),
        (allocation) => {
          const option = derive.buildDonutChartOption(allocation);
          const seriesData = option.series[0].data;

          expect(seriesData.length).toBe(allocation.length);

          // 每个扇区的 value 应精确等于对应的 weight_percent
          for (let i = 0; i < allocation.length; i++) {
            expect(seriesData[i].value).toBeCloseTo(allocation[i].weight_percent, 8);
          }
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("扇区总和在容差内≈100%（当 source 数据正确归一化时）", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 20 }).chain((n) => arbAllocation(n)),
        (allocation) => {
          const option = derive.buildDonutChartOption(allocation);
          const seriesData = option.series[0].data;

          const sum = seriesData.reduce((acc, item) => acc + item.value, 0);

          // 总和应在 100 ± 0.01 内（容差覆盖浮点舍入）
          expect(Math.abs(sum - 100)).toBeLessThan(0.01);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("空输入返回空 series 且不抛错", () => {
    const option = derive.buildDonutChartOption([]);
    expect(option.series[0].data.length).toBe(0);
  });

  it("非数组输入不抛错", () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.constant(null),
          fc.constant(undefined),
          fc.string(),
          fc.integer()
        ),
        (input) => {
          expect(() => derive.buildDonutChartOption(input)).not.toThrow();
          const option = derive.buildDonutChartOption(input);
          expect(option.series[0].data.length).toBe(0);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
