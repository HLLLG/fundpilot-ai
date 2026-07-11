import { describe, expect, it } from "vitest";
import { translateEvidenceText } from "@/lib/decisionText";

describe("translateEvidenceText", () => {
  it("humanizes legacy report enum leaks", () => {
    expect(
      translateEvidenceText("半导体板块机会absent，daily_return数据pending，momentum分位19"),
    ).toBe("半导体板块当前不构成机会，当日涨跌待确认，动量分位19");
  });
});
