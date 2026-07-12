// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { AddHoldingModal } from "@/components/AddHoldingModal";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>新增持有</button>
      <AddHoldingModal
        open={open}
        onClose={() => setOpen(false)}
        onUpload={vi.fn()}
        onManualSubmit={vi.fn()}
      />
    </>
  );
}

it("discloses the configured OCR boundary and restores focus on Escape", () => {
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "新增持有" });
  trigger.focus();
  fireEvent.click(trigger);

  expect(screen.getByRole("dialog", { name: "导入持有" })).toBeInTheDocument();
  expect(screen.getByText(OCR_PRIVACY_COPY.uploadNotice)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
  expect(document.body.style.overflow).toBe("hidden");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});

it("uses associated labels and keeps an incomplete manual draft un-submittable", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "新增持有" }));
  fireEvent.click(screen.getByRole("button", { name: "手动输入" }));

  expect(screen.getByRole("textbox", { name: "基金名称" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "持有金额" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "完成（0）" })).toBeDisabled();
});
