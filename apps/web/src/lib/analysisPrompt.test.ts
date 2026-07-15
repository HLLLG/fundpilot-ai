import { describe, expect, it } from "vitest";
import { activeAnalysisRolePrompt } from "@/lib/analysisPrompt";

describe("activeAnalysisRolePrompt", () => {
  it("does not leak a legacy local default when remote prompt loading fails", () => {
    expect(
      activeAnalysisRolePrompt({
        role_prompt: "旧版本默认提示词，不应作为用户附录发送",
        default_role_prompt: "当前默认提示词",
        is_custom: false,
      }),
    ).toBeUndefined();
  });

  it("returns an explicitly enabled custom prompt", () => {
    expect(
      activeAnalysisRolePrompt({
        role_prompt: "我的风险偏好补充",
        default_role_prompt: "",
        is_custom: true,
      }),
    ).toBe("我的风险偏好补充");
  });
});
