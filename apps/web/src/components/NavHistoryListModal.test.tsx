// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { NavHistoryListModal } from "@/components/NavHistoryListModal";
import { fetchFundNavHistoryPage } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchFundNavHistoryPage: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(fetchFundNavHistoryPage).mockResolvedValue({
    fund_code: "000001",
    fund_name: "示例基金",
    source: "test",
    points: [],
    has_more: false,
    next_before: null,
  });
});

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
  vi.clearAllMocks();
});

function Harness() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开历史净值
      </button>
      {open ? (
        <NavHistoryListModal
          fundCode="000001"
          fundName="示例基金"
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

it("manages focus, scroll locking, and top-level Escape", async () => {
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "打开历史净值" });
  trigger.focus();
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "历史净值" });
  const close = screen.getByRole("button", { name: "关闭" });
  expect(dialog).toHaveAccessibleDescription("示例基金");
  expect(close).toHaveClass("touch-target");
  expect(close).toHaveFocus();
  expect(document.body.style.overflow).toBe("hidden");
  await waitFor(() => expect(screen.getByText("暂无历史净值")).toBeInTheDocument());

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});
