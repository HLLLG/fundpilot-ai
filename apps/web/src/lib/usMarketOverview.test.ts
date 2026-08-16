import { describe, expect, it } from "vitest";

import {
  acceptUsMarketFresh,
  formatUsEtClock,
  formatUsIndexCaption,
  US_INDEX_LIVE_REFRESH_INTERVAL_MS,
  US_SESSION_LABEL,
  usRefreshIntervalMs,
} from "@/lib/usMarketOverview";

// 从函数签名推导参数类型，避免依赖尚未在 api.ts 落地的 UsMarketSnapshot 类型（任务 9.1）。
type UsMarketSnapshotArg = Parameters<typeof acceptUsMarketFresh>[0];

const LIVE_INTERVAL_MS = US_INDEX_LIVE_REFRESH_INTERVAL_MS;

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

  it("does not poll while the US session is closed", () => {
    expect(usRefreshIntervalMs("closed")).toBeNull();
    expect(usRefreshIntervalMs(undefined)).toBeNull();
  });

  it("keeps the same live interval across active sessions", () => {
    expect(usRefreshIntervalMs("pre_market")).toBe(usRefreshIntervalMs("regular"));
    expect(usRefreshIntervalMs("after_hours")).toBe(usRefreshIntervalMs("regular"));
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

describe("formatUsEtClock", () => {
  it("prints the New York wall-clock date and hour only", () => {
    // 北京 14:19 = UTC 06:19 = 美东夏令时 02 时
    expect(formatUsEtClock(new Date("2026-08-16T06:19:00.000Z"))).toBe(
      "2026-08-16 02时 ET",
    );
  });

  it("keeps the Eastern calendar date when Beijing has already rolled over", () => {
    // 北京 10:00 = UTC 02:00 = 美东夏令时前一天 22 时
    expect(formatUsEtClock(new Date("2026-08-17T02:00:00.000Z"))).toBe(
      "2026-08-16 22时 ET",
    );
  });
});

describe("formatUsIndexCaption", () => {
  it("joins session label with the snapshot collection hour", () => {
    expect(
      formatUsIndexCaption({
        session_kind: "closed",
        updated_at: "2026-08-16T02:19:00-04:00",
      }),
    ).toBe("休市 · 2026-08-16 02时 ET");
  });

  it("falls back to et_date when the snapshot has no collection time", () => {
    expect(
      formatUsIndexCaption({
        session_kind: "closed",
        et_date: "2026-08-16",
      }),
    ).toBe("休市 · 2026-08-16 ET");
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
