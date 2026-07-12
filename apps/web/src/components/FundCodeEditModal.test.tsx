// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { FundCodeEditModal } from "@/components/FundCodeEditModal";
import { searchFunds } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  searchFunds: vi.fn(async () => []),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  document.body.style.overflow = "";
});

function Harness() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开代码修正
      </button>
      <FundCodeEditModal
        open={open}
        fundCode="000001"
        fundName="示例基金"
        onClose={() => setOpen(false)}
        onSave={vi.fn()}
      />
    </>
  );
}

it("manages focus, scroll locking, Escape, and labelled fields", () => {
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "打开代码修正" });
  trigger.focus();
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "修正基金代码" });
  const close = screen.getByRole("button", { name: "关闭" });
  expect(dialog).toHaveAccessibleDescription(
    "OCR 或名称匹配错误时可手动改码，将从东财档案迁移到正确代码。",
  );
  expect(screen.getByRole("textbox", { name: "基金代码" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "基金名称" })).toBeInTheDocument();
  expect(close).toHaveClass("touch-target");
  expect(close).toHaveFocus();
  expect(document.body.style.overflow).toBe("hidden");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});

it("keeps every embedded fund-search control named and at least 44px tall", async () => {
  vi.mocked(searchFunds).mockResolvedValueOnce([
    { fund_code: "110023", fund_name: "示例搜索基金" },
  ]);
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "打开代码修正" }));
  fireEvent.click(screen.getByRole("button", { name: "搜索选码" }));

  expect(await screen.findByRole("textbox", { name: "搜索基金" })).toHaveClass("min-h-11");
  expect(screen.getByRole("button", { name: "关闭基金搜索" })).toHaveClass("touch-target");
  expect(
    await screen.findByRole("button", { name: "选择 示例搜索基金（110023）" }),
  ).toHaveClass("min-h-11");

  fireEvent.keyDown(screen.getByRole("textbox", { name: "搜索基金" }), { key: "Escape" });
  expect(screen.getByRole("dialog", { name: "修正基金代码" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "基金搜索结果" })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "搜索选码" })).toHaveFocus());
});
