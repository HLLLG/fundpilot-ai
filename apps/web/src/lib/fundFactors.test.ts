import { describe, expect, it } from "vitest";

import {
  factorLabel,
  factorPercentileHint,
  formatPercentile,
  gradeTone,
  compositeSummary,
} from "@/lib/fundFactors";
import type { FundFactorScore } from "@/lib/api";

describe("formatPercentile", () => {
  it("formats null as em dash", () => {
    expect(formatPercentile(null)).toBe("—");
  });

  it("formats percentile as rank text", () => {
    expect(formatPercentile(78)).toBe("78");
    expect(formatPercentile(0)).toBe("0");
    expect(formatPercentile(100)).toBe("100");
  });
});

describe("gradeTone", () => {
  it("maps grades to tones", () => {
    expect(gradeTone("A")).toBe("good");
    expect(gradeTone("B")).toBe("neutral");
    expect(gradeTone("C")).toBe("warn");
    expect(gradeTone("D")).toBe("danger");
    expect(gradeTone(null)).toBe("neutral");
  });
});

describe("factorLabel", () => {
  it("returns Chinese label per factor key", () => {
    expect(factorLabel("momentum")).toBe("动量");
    expect(factorLabel("risk_adjusted")).toBe("风险调整收益");
    expect(factorLabel("drawdown")).toBe("回撤控制");
    expect(factorLabel("size")).toBe("规模");
  });
});

describe("factorPercentileHint", () => {
  it("handles null percentile", () => {
    expect(factorPercentileHint("momentum", null)).toContain("数据不足");
  });

  it("describes strong vs weak", () => {
    expect(factorPercentileHint("momentum", 90)).toContain("前列");
    expect(factorPercentileHint("momentum", 10)).toContain("靠后");
  });
});

describe("compositeSummary", () => {
  const fund = (score: number | null): FundFactorScore => ({
    fund_code: "000001",
    fund_name: "测试基金",
    in_universe: true,
    composite_score: score,
    composite_grade: score == null ? null : "A",
    factors: {
      momentum: { raw: 1, z: 1, percentile: 80 },
      risk_adjusted: { raw: 1, z: 1, percentile: 60 },
      drawdown: { raw: -10, z: 0, percentile: 50 },
      size: { raw: 1, z: 0, percentile: 50 },
    },
  });

  it("handles missing score", () => {
    expect(compositeSummary(fund(null))).toContain("数据不足");
  });

  it("mentions the percentile when present", () => {
    expect(compositeSummary(fund(78))).toContain("78");
  });
});
