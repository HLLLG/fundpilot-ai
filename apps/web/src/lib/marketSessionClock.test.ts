import { describe, expect, it } from "vitest";

import { msUntilNextWeekdayWallClock } from "@/lib/marketSessionClock";

describe("msUntilNextWeekdayWallClock", () => {
  it("waits until the same weekday morning when still before the open", () => {
    const now = new Date("2026-08-17T00:00:00.000Z"); // 北京 08:00 周一
    const waitMs = msUntilNextWeekdayWallClock("Asia/Shanghai", 9, 30, now);
    expect(new Date(now.getTime() + waitMs).toISOString()).toBe("2026-08-17T01:30:00.000Z");
  });

  it("skips the weekend to the next Monday open", () => {
    const now = new Date("2026-08-16T06:19:00.000Z"); // 周日北京下午
    const waitMs = msUntilNextWeekdayWallClock("Asia/Shanghai", 9, 30, now);
    expect(new Date(now.getTime() + waitMs).toISOString()).toBe("2026-08-17T01:30:00.000Z");
  });

  it("uses the next weekday when today's open has already passed", () => {
    const now = new Date("2026-08-14T08:00:00.000Z"); // 周五北京 16:00
    const waitMs = msUntilNextWeekdayWallClock("Asia/Shanghai", 9, 30, now);
    expect(new Date(now.getTime() + waitMs).toISOString()).toBe("2026-08-17T01:30:00.000Z");
  });

  it("targets the next US pre-market open in Eastern time", () => {
    const now = new Date("2026-08-16T06:19:00.000Z"); // 周日美东凌晨
    const waitMs = msUntilNextWeekdayWallClock("America/New_York", 4, 0, now);
    expect(new Date(now.getTime() + waitMs).toISOString()).toBe("2026-08-17T08:00:00.000Z");
  });
});
