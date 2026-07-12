// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { InlineNotice } from "@/components/InlineNotice";

afterEach(cleanup);

it("announces errors assertively and keeps actions explicit", () => {
  const retry = vi.fn();
  render(<InlineNotice tone="error" message="持仓加载失败" action={{ label: "重试", onClick: retry }} />);
  expect(screen.getByRole("alert")).toHaveAttribute("aria-live", "assertive");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("uses a polite status for successful feedback", () => {
  render(<InlineNotice tone="success" message="持仓已保存" />);
  expect(screen.getByRole("status")).toHaveTextContent("持仓已保存");
});
