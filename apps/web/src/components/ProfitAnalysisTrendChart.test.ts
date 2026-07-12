import { describe, expect, it } from "vitest";

import {
  buildSegmentedLinePath,
  formatProfitAxisLabel,
  mapProfitTrendValues,
} from "@/components/ProfitAnalysisTrendChart";

describe("ProfitAnalysisTrendChart data mapping", () => {
  it("keeps a real zero while preserving missing and non-finite values as gaps", () => {
    expect(
      mapProfitTrendValues([
        { date: "2026-07-01", portfolio_percent: null, index_percent: 1.2 },
        { date: "2026-07-02", portfolio_percent: undefined, index_percent: null },
        { date: "2026-07-03", portfolio_percent: 0, index_percent: Number.NaN },
        {
          date: "2026-07-04",
          portfolio_percent: Number.POSITIVE_INFINITY,
          index_percent: -0.5,
        },
      ]),
    ).toEqual([
      { portfolioPercent: null, indexPercent: 1.2 },
      { portfolioPercent: null, indexPercent: null },
      { portfolioPercent: 0, indexPercent: null },
      { portfolioPercent: null, indexPercent: -0.5 },
    ]);
  });

  it("starts a new path segment after a missing point instead of drawing through zero", () => {
    expect(
      buildSegmentedLinePath([
        { x: 0, y: 12 },
        { x: 1, y: 10 },
        { x: 2, y: null },
        { x: 3, y: 8 },
        { x: 4, y: 6 },
      ]),
    ).toBe("M 0 12 L 1 10 M 3 8 L 4 6");
  });

  it("formats positive, negative, and zero percent axis labels explicitly", () => {
    expect(formatProfitAxisLabel(1.25)).toBe("+1.25%");
    expect(formatProfitAxisLabel(-0.75)).toBe("-0.75%");
    expect(formatProfitAxisLabel(0)).toBe("0.00%");
  });
});
