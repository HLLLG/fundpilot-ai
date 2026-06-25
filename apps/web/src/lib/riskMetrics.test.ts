import { describe, expect, it } from "vitest";

import {
  alphaTone,
  betaHint,
  concentrationTone,
  formatRatio,
  formatSignedPercent,
  maxDrawdownTone,
  sharpeHint,
  sharpeTone,
  volatilityTone,
} from "@/lib/riskMetrics";

describe("formatters", () => {
  it("formats missing values as em dash", () => {
    expect(formatRatio(null)).toBe("—");
    expect(formatSignedPercent(undefined)).toBe("—");
  });

  it("adds sign to percent", () => {
    expect(formatSignedPercent(3.1)).toBe("+3.10%");
    expect(formatSignedPercent(-2)).toBe("-2.00%");
  });

  it("rounds ratios", () => {
    expect(formatRatio(0.785)).toBe("0.79");
  });
});

describe("sharpe scale (doc 3.3)", () => {
  it("treats null as neutral", () => {
    expect(sharpeTone(null)).toBe("neutral");
    expect(sharpeHint(null)).toContain("样本不足");
  });

  it("negative sharpe is danger", () => {
    expect(sharpeTone(-0.5)).toBe("danger");
    expect(sharpeHint(-0.5)).toContain("存银行");
  });

  it("sub-1 sharpe is warn", () => {
    expect(sharpeTone(0.8)).toBe("warn");
  });

  it(">=1 sharpe is good", () => {
    expect(sharpeTone(1.5)).toBe("good");
  });
});

describe("max drawdown tone (doc 3.2)", () => {
  it("shallow drawdown is good", () => {
    expect(maxDrawdownTone(-5)).toBe("good");
  });
  it("deep drawdown is danger", () => {
    expect(maxDrawdownTone(-30)).toBe("danger");
  });
});

describe("volatility tone (doc 3.1)", () => {
  it("low vol good, high vol danger", () => {
    expect(volatilityTone(8)).toBe("good");
    expect(volatilityTone(30)).toBe("danger");
  });
});

describe("alpha / beta / concentration", () => {
  it("positive alpha is good", () => {
    expect(alphaTone(3)).toBe("good");
    expect(alphaTone(-1)).toBe("danger");
  });
  it("beta near 1 reads as in-sync", () => {
    expect(betaHint(1.0)).toContain("同步");
  });
  it("high HHI is danger", () => {
    expect(concentrationTone(0.5)).toBe("danger");
    expect(concentrationTone(0.1)).toBe("good");
  });
});
