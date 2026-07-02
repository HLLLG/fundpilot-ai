import { describe, expect, it } from "vitest";

import {
  acceptUsMarketFresh,
  US_SESSION_LABEL,
  usRefreshIntervalMs,
} from "@/lib/usMarketOverview";

// 从函数签名推导参数类型，避免依赖尚未在 api.ts 落地的 UsMarketSnapshot 类型（任务 9.1）。
type UsMarketSnapshotArg = Parameters<typeof acceptUsMarketFresh>[0];

const LIVE_INTERVAL_MS = 1_200_000;
const IDLE_INTERVAL_MS = 10_800_000;

function makeSnapshot(available: boolean): UsMarketSnapshotArg {
  return { available } as UsMarketSnapshotArg;
}

describe("usRefreshIntervalMs", () => {
  // 与服务端 market_shared 对齐：美股活跃时段 20min 刷新。
  it("returns the live interval for pre_market", () => {
    expect(usRefreshIntervalMs("pre_market")).toBe(LIVE_INTERVAL_MS);
  });

  it("returns the live interval for regular", () => {
    expect(usRefreshIntervalMs("regular")).toBe(LIVE_INTERVAL_MS);
  });

  it("returns the live interval for after_hours", () => {
    expect(usRefreshIntervalMs("after_hours")).toBe(LIVE_INTERVAL_MS);
  });

  // 休市低频刷新，避免用户请求打源。
  it("returns the idle interval for closed", () => {
    expect(usRefreshIntervalMs("closed")).toBe(IDLE_INTERVAL_MS);
  });

  it("keeps live-session intervals shorter than rest-session intervals", () => {
    expect(usRefreshIntervalMs("pre_market")).toBe(usRefreshIntervalMs("regular"));
    expect(usRefreshIntervalMs("after_hours")).toBe(usRefreshIntervalMs("regular"));
    expect(usRefreshIntervalMs("regular")).toBeLessThan(usRefreshIntervalMs("closed"));
  });
});

describe("US_SESSION_LABEL", () => {
  it("maps every session kind to its Chinese label", () => {
    expect(US_SESSION_LABEL).toEqual({
      pre_market: "盘前交易中",
      regular: "盘中",
      after_hours: "盘后",
      closed: "休市",
    });
  });
});

describe("acceptUsMarketFresh", () => {
  // 需求 5.x：仅当新快照 available 为真时才替换旧数据（stale-while-revalidate）。
  it("accepts a fresh snapshot when available is true", () => {
    expect(acceptUsMarketFresh(makeSnapshot(true))).toBe(true);
  });

  it("rejects a fresh snapshot when available is false", () => {
    expect(acceptUsMarketFresh(makeSnapshot(false))).toBe(false);
  });

  it("rejects when available is missing or snapshot is nullish", () => {
    expect(acceptUsMarketFresh({} as UsMarketSnapshotArg)).toBe(false);
    expect(acceptUsMarketFresh(undefined as unknown as UsMarketSnapshotArg)).toBe(false);
    expect(acceptUsMarketFresh(null as unknown as UsMarketSnapshotArg)).toBe(false);
  });
});
