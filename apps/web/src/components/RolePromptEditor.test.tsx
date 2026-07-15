// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { RolePromptEditor } from "@/components/RolePromptEditor";

afterEach(() => cleanup());

it("presents role_prompt as a bounded user appendix, not an editable system contract", () => {
  const onChange = vi.fn();
  render(<RolePromptEditor value="" onChange={onChange} />);

  expect(screen.getByText("未添加分析偏好；系统将使用内置安全契约。")).toBeInTheDocument();
  expect(
    screen.getByText(/系统事实、动作、金额、引用与 JSON 契约始终由服务端固定/),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /编辑/ }));
  const editor = screen.getByRole("textbox", { name: "大模型分析偏好附录" });
  expect(editor).toHaveAttribute("maxlength", "2000");
  expect(editor).toHaveAttribute(
    "placeholder",
    expect.stringContaining("不能修改系统事实、动作、金额或输出格式"),
  );
  fireEvent.change(editor, { target: { value: "结论先行，优先说明回撤" } });
  expect(onChange).toHaveBeenCalledWith("结论先行，优先说明回撤");
});
