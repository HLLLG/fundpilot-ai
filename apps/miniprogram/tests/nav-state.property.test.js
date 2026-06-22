// Feature: miniprogram-web-parity, Property 5: 导航状态往返
//
// Property 5 (design.md):
//   For any 页面键与子视图/预填值 (pageKey, value)，经 nav-state.js 写入后读取
//   应得到与写入深度相等的值（round-trip）。
//
// Validates: Requirements 3.5, 10.6, 11.5
//
// 实现要点：
//   - 用 createMemoryStorage() 作为注入的存储后端，脱离 wx.* 在 Node 环境下测试。
//   - 用 fast-check 生成任意字符串页面键（fc.string()，含 "__proto__"/"constructor"
//     等原型链键）与任意 JSON 可序列化值（fc.jsonValue()）。
//   - 断言通用 setState/getState 往返保真；命名 helper（dipRadarSector/
//     discoveryFocusSectors/discoveryScanMode）与 subView 往返保真。
//   - nav-state 通过 JSON 序列化实现往返，故与规范 JSON 往返形态深度相等。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// nav-state.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const navStateModule = require("../utils/nav-state");

const { createNavState, createMemoryStorage } = navStateModule;

const NUM_RUNS = 200; // ≥ 100 次迭代

// nav-state 通过 JSON 序列化实现往返；写入 undefined 时编码为 null。
function canonical(value) {
  return JSON.parse(JSON.stringify(value === undefined ? null : value));
}

describe("Property 5: 导航状态往返", () => {
  it("通用 setState(key, value) 后 getState(key) 深度相等于写入值（往返保真）", () => {
    fc.assert(
      fc.property(
        // 任意页面/状态键，含 "__proto__"/"constructor" 等原型链键。
        fc.string(),
        // 任意 JSON 可序列化值：null/bool/number/string/array/object 的嵌套组合。
        fc.jsonValue(),
        (key, value) => {
          const nav = createNavState(createMemoryStorage());

          nav.setState(key, value);
          const roundTripped = nav.getState(key);

          expect(roundTripped).toStrictEqual(canonical(value));
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("subView(pageKey, view) 往返保真，且不同页面键互不干扰", () => {
    fc.assert(
      fc.property(
        fc.string(),
        fc.jsonValue(),
        fc.string(),
        fc.jsonValue(),
        (pageKeyA, viewA, pageKeyB, viewB) => {
          // 仅在两个页面键不同的情况下断言互不干扰。
          fc.pre(pageKeyA !== pageKeyB);

          const nav = createNavState(createMemoryStorage());

          nav.setSubView(pageKeyA, viewA);
          nav.setSubView(pageKeyB, viewB);

          expect(nav.getSubView(pageKeyA)).toStrictEqual(canonical(viewA));
          expect(nav.getSubView(pageKeyB)).toStrictEqual(canonical(viewB));
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("命名预填 helper（dipRadarSector / discoveryScanMode）往返保真", () => {
    fc.assert(
      fc.property(
        // 板块预填（Req 10.6）通常为字符串板块名。
        fc.string(),
        // 扫描模式（Req 11.5）如 'dip_swing'。
        fc.string(),
        (sector, mode) => {
          const nav = createNavState(createMemoryStorage());

          nav.setDipRadarSector(sector);
          nav.setDiscoveryScanMode(mode);

          expect(nav.getDipRadarSector()).toStrictEqual(canonical(sector));
          expect(nav.getDiscoveryScanMode()).toStrictEqual(canonical(mode));
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  it("discoveryFocusSectors（数组）往返保真，未写入时返回空数组", () => {
    fc.assert(
      fc.property(fc.array(fc.string()), (sectors) => {
        const nav = createNavState(createMemoryStorage());

        // 未写入：返回空数组而非抛错。
        expect(nav.getDiscoveryFocusSectors()).toStrictEqual([]);

        nav.setDiscoveryFocusSectors(sectors);
        expect(nav.getDiscoveryFocusSectors()).toStrictEqual(canonical(sectors));
      }),
      { numRuns: NUM_RUNS }
    );
  });
});
