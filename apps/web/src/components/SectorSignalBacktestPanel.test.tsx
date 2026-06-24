import { describe, expect, it } from "vitest";
import { confidenceTone } from "./SectorSignalBacktestPanel";

describe("confidenceTone", () => {
  it("maps 高 to green", () => {
    expect(confidenceTone("高")).toBe("green");
  });
  it("maps 中 to amber", () => {
    expect(confidenceTone("中")).toBe("amber");
  });
  it("maps 低 to red", () => {
    expect(confidenceTone("低")).toBe("red");
  });
  it("maps 不足/unknown/undefined to blue", () => {
    expect(confidenceTone("不足")).toBe("blue");
    expect(confidenceTone("???")).toBe("blue");
    expect(confidenceTone(undefined)).toBe("blue");
  });
});
