// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { useRef, useState } from "react";
import "@testing-library/jest-dom/vitest";

import { useDialogA11y } from "@/lib/useDialogA11y";

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

function Harness() {
  const [open, setOpen] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open,
    onClose: () => setOpen(false),
    initialFocusRef: closeRef,
  });

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>打开</button>
      {open ? (
        <div ref={dialogRef} role="dialog" aria-modal="true" aria-label="测试弹层" tabIndex={-1}>
          <button ref={closeRef} type="button" onClick={() => setOpen(false)}>关闭</button>
          <input aria-label="输入内容" />
        </div>
      ) : null}
    </>
  );
}

it("focuses the initial control, locks scroll, and restores the trigger", () => {
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "打开" });
  trigger.focus();
  fireEvent.click(trigger);
  expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
  expect(document.body.style.overflow).toBe("hidden");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});

it("wraps focus inside the topmost dialog", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "打开" }));
  const close = screen.getByRole("button", { name: "关闭" });
  const input = screen.getByRole("textbox", { name: "输入内容" });

  close.focus();
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  expect(input).toHaveFocus();
  fireEvent.keyDown(document, { key: "Tab" });
  expect(close).toHaveFocus();
});
