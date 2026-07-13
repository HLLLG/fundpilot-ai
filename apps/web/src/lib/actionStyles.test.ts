import { describe, expect, it } from "vitest";

import { actionBadgeClass, actionTone } from "@/lib/actionStyles";

describe("discovery action styles", () => {
  it("maps the three discovery decision actions to distinct semantic tones", () => {
    expect(actionTone("分批买入")).toBe("add");
    expect(actionTone("等待回调")).toBe("pause");
    expect(actionTone("建议关注")).toBe("watch");
  });

  it("keeps pause semantics ahead of embedded add words", () => {
    expect(actionTone("暂停加仓")).toBe("pause");
  });

  it("provides a complete reusable badge shape", () => {
    expect(actionBadgeClass("分批买入")).toContain("rounded-full");
    expect(actionBadgeClass("分批买入")).toContain("border");
    expect(actionBadgeClass("分批买入")).toContain("bg-emerald-50");
  });
});
