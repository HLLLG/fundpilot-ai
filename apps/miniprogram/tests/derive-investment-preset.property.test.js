// Feature: miniprogram-web-parity, Property 18: 投资预设联动
//
// Property 18 (design.md):
//   For any 在 `conservative_hold` 与 `aggressive_swing` 之间的预设切换，
//   联动的风控字段被设为该预设对应的预期值，且 `investment_preset` 字段
//   与所选预设保持同步。
//
// Validates: Requirements 18.3
//
// 实现要点：
//   - 用 fast-check 生成任意预设切换序列（在两个有效预设间随机选择），
//     断言每次 applyInvestmentPreset(preset) 返回的风控字段等于该预设
//     的预期默认值，且 investment_preset 与所选预设一致。
//   - 断言切换是无状态的：结果只取决于当前预设，与切换历史无关。
//   - 断言无效预设返回 null（防御性）。
//   - 断言返回对象与 PRESET_DEFAULTS 内部对象不共享引用（纯函数性）。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// derive.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const { applyInvestmentPreset, PRESET_DEFAULTS } = require("../utils/derive");

const NUM_RUNS = 200; // ≥ 100 次迭代

// 两个有效预设名。
const VALID_PRESETS = ["conservative_hold", "aggressive_swing"];

// 预期的联动风控字段（与 design.md / PRESET_DEFAULTS 对齐）。
const EXPECTED = {
  conservative_hold: {
    investment_preset: "conservative_hold",
    max_drawdown_percent: 10,
    concentration_limit_percent: 30,
    prefer_dca: true,
    avoid_chasing: true,
    decision_style: "conservative",
  },
  aggressive_swing: {
    investment_preset: "aggressive_swing",
    max_drawdown_percent: 25,
    concentration_limit_percent: 50,
    prefer_dca: false,
    avoid_chasing: false,
    decision_style: "aggressive",
  },
};

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** 生成单个有效预设名。 */
const presetArb = fc.constantFrom(...VALID_PRESETS);

/** 生成预设切换序列（1..20 次切换，覆盖单次与多次连续切换）。 */
const presetSequenceArb = fc.array(presetArb, { minLength: 1, maxLength: 20 });

// ---------------------------------------------------------------------------
// Properties
// ---------------------------------------------------------------------------

describe("Property 18: 投资预设联动", () => {
  it("任意预设切换序列：风控字段被设为该预设预期值，investment_preset 同步", () => {
    fc.assert(
      fc.property(presetSequenceArb, (sequence) => {
        for (const preset of sequence) {
          const result = applyInvestmentPreset(preset);

          // 必须返回非空对象。
          expect(result).not.toBeNull();
          expect(typeof result).toBe("object");

          const expected = EXPECTED[preset];

          // investment_preset 与所选预设保持同步。
          expect(result.investment_preset).toBe(preset);

          // 全部联动风控字段被设为该预设的预期值。
          expect(result.max_drawdown_percent).toBe(expected.max_drawdown_percent);
          expect(result.concentration_limit_percent).toBe(
            expected.concentration_limit_percent
          );
          expect(result.prefer_dca).toBe(expected.prefer_dca);
          expect(result.avoid_chasing).toBe(expected.avoid_chasing);
          expect(result.decision_style).toBe(expected.decision_style);

          // 完整对象等价（确保没有遗漏/多余字段）。
          expect(result).toEqual(expected);
        }
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("切换无状态：结果只取决于当前预设，与历史切换无关", () => {
    fc.assert(
      fc.property(presetArb, presetArb, (first, second) => {
        // 先切到 first，再切到 second。
        applyInvestmentPreset(first);
        const afterSwitch = applyInvestmentPreset(second);

        // 直接切到 second 的结果。
        const direct = applyInvestmentPreset(second);

        // 两者必须等价（无状态）。
        expect(afterSwitch).toEqual(direct);
        expect(afterSwitch).toEqual(EXPECTED[second]);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("无效预设返回 null（防御性）", () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.constant(null),
          fc.constant(undefined),
          fc.constant(""),
          fc.string().filter((s) => !VALID_PRESETS.includes(s)),
          fc.integer()
        ),
        (invalid) => {
          expect(applyInvestmentPreset(invalid)).toBeNull();
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("返回对象不与 PRESET_DEFAULTS 内部对象共享引用（纯函数性）", () => {
    fc.assert(
      fc.property(presetArb, (preset) => {
        const result = applyInvestmentPreset(preset);
        // 不是同一引用（已拷贝）。
        expect(result).not.toBe(PRESET_DEFAULTS[preset]);
        // 修改返回值不影响内部默认值。
        result.max_drawdown_percent = -999;
        expect(PRESET_DEFAULTS[preset].max_drawdown_percent).not.toBe(-999);
      }),
      { numRuns: NUM_RUNS }
    );
  });
});
