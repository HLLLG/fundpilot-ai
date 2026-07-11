// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportChatDrawer } from "@/components/ReportChatDrawer";

vi.mock("@/components/ReportChatPanel", () => ({
  ReportChatPanel: () => <input aria-label="聊天输入" />,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchReportChatHistory: vi.fn(() => new Promise(() => undefined)),
  };
});

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

it("opens an accessible report chat dialog", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  expect(screen.getByRole("dialog", { name: "追问这份日报" })).toBeInTheDocument();
  expect(document.body.style.overflow).toBe("hidden");
});

it("closes on Escape and restores focus to the trigger", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  const trigger = screen.getByRole("button", { name: "追问这份日报" });
  fireEvent.click(trigger);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});

it("closes from the explicit close control", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  fireEvent.click(screen.getByRole("button", { name: "关闭追问助手" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("closes when the backdrop is pressed", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  fireEvent.mouseDown(screen.getByTestId("report-chat-backdrop"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("wraps keyboard focus inside the open drawer", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  const close = screen.getByRole("button", { name: "关闭追问助手" });
  expect(close).toHaveFocus();
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  expect(screen.getByRole("textbox", { name: "聊天输入" })).toHaveFocus();
});

it("preserves the legacy compact chat surface during the drawer migration", async () => {
  const { ReportChatPanel } = await vi.importActual<
    typeof import("@/components/ReportChatPanel")
  >("@/components/ReportChatPanel");

  render(<ReportChatPanel reportId="report-1" reportTitle="日报" compact />);

  expect(screen.getByTestId("report-chat-panel")).toHaveClass(
    "h-[min(92vh,960px)]",
    "min-h-[min(72vh,520px)]",
    "lg:min-h-[640px]",
  );
});
