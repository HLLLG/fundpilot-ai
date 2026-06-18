import { describe, expect, it } from "vitest";

import {
  acceptUsMarketFresh,
  US_SESSION_LABEL,
  usRefreshIntervalMs,
} from "@/lib/usMarketOverview";

// 从函数签名推导参数类型，避免依赖尚未在 api.ts 落地的 UsMarketSnapshot 类型（任务 9.1）。
type UsMarketSnapshotArg = Parameters<typeof acceptUsMarketFresh>[0];

const SHORT_INTERVAL_MS = 45_000;
const LONG_INTERVAL_MS = 300_000;

function makeSnapshot(available: boolean): UsMarketSnapshotArg {
  return { available } as UsMarketSnapshotArg;
}

describe("usRefreshIntervalMs", () => {
  // 需求 5.1：盘前 / 盘中高频刷新（短间隔）。
  it("returns the short interval for pre_market", () => {
    expect(usRefreshIntervalMs("pre_market")).toBe(SHORT_INTERVAL_MS);
  });

  it("returns the short interval for regular", () => {
    expect(usRefreshIntervalMs("regular")).toBe(SHORT_INTERVAL_MS);
  });

  // 需求 5.2：盘后 / 休市低频刷新（长间隔）。
  it("returns the long interval for after_hours", () => {
    expect(usRefreshIntervalMs("after_hours")).toBe(LONG_INTERVAL_MS);
  });

  it("returns the long interval for closed", () => {
    expect(usRefreshIntervalMs("closed")).toBe(LONG_INTERVAL_MS);
  });

  it("keeps live-session intervals shorter than rest-session intervals", () => {
    expect(usRefreshIntervalMs("pre_market")).toBe(usRefreshIntervalMs("regular"));
    expect(usRefreshIntervalMs("after_hours")).toBe(usRefreshIntervalMs("closed"));
    expect(usRefreshIntervalMs("regular")).toBeLessThan(usRefreshIntervalMs("after_hours"));
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
