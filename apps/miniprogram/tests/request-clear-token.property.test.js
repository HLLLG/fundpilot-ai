// Feature: miniprogram-web-parity, Property 3: 401 清登态决策
//
// Property 3 (design.md):
//   For any 响应状态码与入口标记组合 (statusCode, isAuthEntrypoint, allowUnauthorized)，
//   当且仅当 statusCode === 401 且非登录/注册入口且未显式允许未授权时，
//   决策函数判定应「清除 token 并跳登录」。
//
// Validates: Requirements 2.3
//
// 实现要点：
//   - 用 fast-check 生成任意状态码（覆盖 401 与非 401）与两个布尔入口标记。
//   - 断言 shouldClearToken 当且仅当 (statusCode === 401 且 !isAuthEntrypoint 且 !allowUnauthorized) 时返回 true。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// request.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const reqMod = require("../utils/request");

const NUM_RUNS = 200; // ≥ 100 次迭代

describe("Property 3: 401 清登态决策", () => {
  it("当且仅当 401 且非鉴权入口且未显式允许未授权时判定清 token", () => {
    fc.assert(
      fc.property(
        // 覆盖常见 HTTP 状态码并显式纳入 401，确保两侧分支都被采样。
        fc.oneof(
          fc.constant(401),
          fc.integer({ min: 100, max: 599 }),
        ),
        fc.boolean(),
        fc.boolean(),
        (statusCode, isAuthEntrypoint, allowUnauthorized) => {
          const actual = reqMod.shouldClearToken(
            statusCode,
            isAuthEntrypoint,
            allowUnauthorized,
          );
          const expected =
            statusCode === 401 && !isAuthEntrypoint && !allowUnauthorized;

          // 双向蕴含（iff）：决策结果与规范条件严格一致。
          expect(actual).toBe(expected);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it("非 401 状态码在任意入口标记组合下都不清 token", () => {
    fc.assert(
      fc.property(
        fc
          .integer({ min: 100, max: 599 })
          .filter((code) => code !== 401),
        fc.boolean(),
        fc.boolean(),
        (statusCode, isAuthEntrypoint, allowUnauthorized) => {
          expect(
            reqMod.shouldClearToken(statusCode, isAuthEntrypoint, allowUnauthorized),
          ).toBe(false);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});
