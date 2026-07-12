// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { DashboardNav } from "@/components/DashboardNav";

afterEach(cleanup);

it("exposes semantic navigation and the current destination", () => {
  render(
    <DashboardNav
      activeTab="holdings"
      onSelect={vi.fn()}
      onSelectHistory={vi.fn()}
    />,
  );
  expect(screen.getAllByRole("navigation", { name: "主导航" })).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "持仓" })[0]).toHaveAttribute("aria-current", "page");
});

it("moves focus through the mobile more menu and restores it on Escape", () => {
  render(
    <DashboardNav
      activeTab="holdings"
      reportTabUnread
      onSelect={vi.fn()}
      onSelectHistory={vi.fn()}
    />,
  );
  const trigger = screen.getByRole("button", { name: "更多导航，有新内容" });
  fireEvent.click(trigger);
  const first = screen.getByRole("menuitem", { name: "发现基金" });
  const second = screen.getByRole("menuitem", { name: "生成日报" });
  expect(first).toHaveFocus();
  fireEvent.keyDown(document, { key: "ArrowDown" });
  expect(second).toHaveFocus();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("menu", { name: "更多页面" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
