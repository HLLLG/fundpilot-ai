import { describe, expect, it } from "vitest";
import { buildFlatIntradayPoints } from "@/components/IntradayPercentChart";

describe("buildFlatIntradayPoints", () => {
  it("returns a two-point series spanning the full trading session at a constant percent", () => {
    const points = buildFlatIntradayPoints(-1.04);

    expect(points).toEqual([
      { time: "09:30", percent: -1.04 },
      { time: "15:00", percent: -1.04 },
    ]);
  });

  it("keeps both endpoints equal so the rendered line is perfectly flat", () => {
    const points = buildFlatIntradayPoints(2.5);

    expect(points[0].percent).toBe(points[1].percent);
  });
});
