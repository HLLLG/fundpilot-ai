// Feature: miniprogram-web-parity, Property 4: 错误文案提取
//
// Property 4 (design.md):
//   For any 状态码 ≥ 400 的响应，错误对象的 message 在响应体含字符串 `detail`
//   时等于该 `detail`，否则为既定默认文案；不抛出未捕获异常。
//
// Validates: Requirements 2.5
//
// 实现要点：
//   - 用 fast-check 生成任意 ≥400 的状态码与任意形态的响应体
//     （含 string detail / 非字符串 detail / 缺失 detail / 非对象 body）。
//   - 断言：当 body 含非空字符串 detail 时 message === detail；否则 === 默认文案。
//   - 断言：extractError 永远返回 Error 实例且不抛出未捕获异常。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// request.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const req = require("../utils/request");

const NUM_RUNS = 200; // ≥ 100 次迭代

const DEFAULT_ERROR_MESSAGE = req.DEFAULT_ERROR_MESSAGE;

describe("Property 4: 错误文案提取", () => {
  it("body 含非空字符串 detail 时 message 等于该 detail，且永不抛异常", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 400, max: 599 }),
        fc.string({ minLength: 1 }),
        (status, detail) => {
          let err;
          expect(() => {
            err = req.extractError(status, { detail: detail });
          }).not.toThrow();
          expect(err).toBeInstanceOf(Error);
          expect(err.message).toBe(detail);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("body 不含可用字符串 detail 时 message 回退为默认文案，且永不抛异常", () => {
    // 生成不含「非空字符串 detail」的各种响应体：
    //   - 缺失 detail 的对象
    //   - detail 为非字符串（数字/布尔/null/对象/数组）
    //   - detail 为空字符串
    //   - 非对象 body（null/undefined/字符串/数字）
    const bodyWithoutValidDetail = fc.oneof(
      fc.constant(undefined),
      fc.constant(null),
      fc.string(),
      fc.integer(),
      fc.boolean(),
      fc.record({ detail: fc.constant("") }),
      fc.record({ detail: fc.oneof(fc.integer(), fc.boolean(), fc.constant(null)) }),
      fc.record({ detail: fc.array(fc.string()) }),
      fc.record({ message: fc.string() }), // 无 detail 键
      fc.object()
    );

    fc.assert(
      fc.property(
        fc.integer({ min: 400, max: 599 }),
        bodyWithoutValidDetail,
        (status, body) => {
          // 过滤掉随机 object 恰好生成了非空字符串 detail 的情况（归入另一条性质）。
          fc.pre(
            !(
              body &&
              typeof body === "object" &&
              typeof body.detail === "string" &&
              body.detail
            )
          );
          let err;
          expect(() => {
            err = req.extractError(status, body);
          }).not.toThrow();
          expect(err).toBeInstanceOf(Error);
          expect(err.message).toBe(DEFAULT_ERROR_MESSAGE);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
