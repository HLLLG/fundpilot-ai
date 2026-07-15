import { describe, expect, it } from "vitest";
import { translateEvidenceText } from "@/lib/decisionText";

describe("translateEvidenceText", () => {
  it("humanizes legacy report enum leaks", () => {
    expect(
      translateEvidenceText("半导体板块机会absent，daily_return数据pending，momentum分位19"),
    ).toBe("半导体板块当前不构成机会，当日涨跌待确认，动量分位19");
  });

  it("explains blocked execution in beginner-facing language", () => {
    expect(
      translateEvidenceText("字段级证据未达到可执行条件，本条仅保留观察/风险复核。"),
    ).toBe("关键信息还不够完整或不够新，先观察，等数据更新后再判断。");
    expect(
      translateEvidenceText("字段级证据未达到时点可用条件，未生成可执行金额"),
    ).toBe("关键信息还不够完整或不够新，因此暂不提供买卖金额。");
  });
});
