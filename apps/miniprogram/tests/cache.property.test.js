// Feature: miniprogram-web-parity, Property 6: 缓存往返
//
// Property 6 (design.md):
//   For any 可序列化数据与缓存键 (key, data)，cache.set(key, data) 后 cache.get(key)
//   应深度相等于 data；未写入键的读取返回空值（null）而非抛错。
//
// Validates: Requirements 4.3, 5.2
//
// 实现要点：
//   - 用 createMemoryStorage() 作为注入的存储后端，脱离 wx.* 在 Node 环境下测试。
//   - 用 fast-check 生成任意 JSON 可序列化数据（fc.jsonValue）与任意字符串键。
//   - 断言 set 后 get 与数据经规范 JSON 往返后深度相等（与 cache 的 encode/decode 语义一致）。
//   - 断言未写入键读取返回 null 且不抛错。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// cache.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const cacheModule = require("../utils/cache");

const { createCache, createMemoryStorage } = cacheModule;

const NUM_RUNS = 200; // ≥ 100 次迭代

describe("Property 6: 缓存往返", () => {
  it("set(key, data) 后 get(key) 深度相等于 data（往返保真）", () => {
    fc.assert(
      fc.property(
        // 任意缓存键（字符串）。
        fc.string(),
        // 任意 JSON 可序列化数据：null/bool/number/string/array/object 的嵌套组合。
        fc.jsonValue(),
        (key, data) => {
          const cache = createCache(createMemoryStorage());

          cache.set(key, data);
          const roundTripped = cache.get(key);

          // cache 通过 JSON 序列化实现往返，故与规范 JSON 往返形态深度相等。
          expect(roundTripped).toStrictEqual(JSON.parse(JSON.stringify(data)));
          // 命中已写入的键。
          expect(cache.has(key)).toBe(true);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("写入后修改原对象不影响已存值（引用隔离）", () => {
    fc.assert(
      fc.property(fc.string(), fc.array(fc.integer()), (key, arr) => {
        const cache = createCache(createMemoryStorage());
        const snapshot = arr.slice();

        cache.set(key, arr);
        arr.push(999); // 写入后篡改原引用。

        // 已存值应等于写入时的快照，不随原引用变化。
        expect(cache.get(key)).toStrictEqual(snapshot);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  it("读取未写入的键返回 null 而非抛错", () => {
    fc.assert(
      fc.property(fc.string(), (key) => {
        const cache = createCache(createMemoryStorage());

        // 未写入键：不抛错且返回空值（null）。
        let result;
        expect(() => {
          result = cache.get(key);
        }).not.toThrow();
        expect(result).toBeNull();
        expect(cache.has(key)).toBe(false);
      }),
      { numRuns: NUM_RUNS }
    );
  });
});
