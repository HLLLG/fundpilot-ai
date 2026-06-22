// Feature: miniprogram-web-parity, Property 2: 鉴权头注入
//
// Property 2 (design.md):
//   For any 已存储的非空 token 与任意请求路径/选项，请求层构造的 header 满足
//   `Authorization === "Bearer " + token`；当无 token 时不注入该头。
//
// Validates: Requirements 2.2
//
// 实现要点：
//   - 用 fast-check 生成任意非空 token 字符串，断言 buildAuthHeader(token) 注入
//     `Authorization: "Bearer " + token`。
//   - 断言对「无 token」（空串/null/undefined）不注入 Authorization 头（返回空对象）。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// request.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const req = require("../utils/request");

const NUM_RUNS = 200; // ≥ 100 次迭代

describe("Property 2: 鉴权头注入", () => {
  it("非空 token 注入 Authorization: \"Bearer \" + token", () => {
    fc.assert(
      fc.property(
        // 任意非空 token 字符串（覆盖 JWT 风格、含特殊字符、超长等）。
        fc.string({ minLength: 1 }),
        (token) => {
          const header = req.buildAuthHeader(token);
          expect(header.Authorization).toBe("Bearer " + token);
          // 仅注入 Authorization 一个键，不引入其他副作用键。
          expect(Object.keys(header)).toEqual(["Authorization"]);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("无 token（空串/null/undefined/0/false）不注入 Authorization 头", () => {
    fc.assert(
      fc.property(
        // 各种 falsy 取值代表「无 token」。
        fc.constantFrom("", null, undefined, 0, false, NaN),
        (token) => {
          const header = req.buildAuthHeader(token);
          expect(header).toEqual({});
          expect("Authorization" in header).toBe(false);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
