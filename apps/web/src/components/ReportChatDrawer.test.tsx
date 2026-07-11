// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportChatDrawer } from "@/components/ReportChatDrawer";
import { installMatchMedia, type MatchMediaController } from "@/test/matchMedia";

const DESKTOP_QUERY = "(min-width: 1280px)";

let matchMedia: MatchMediaController;

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

beforeEach(() => {
  matchMedia = installMatchMedia({ [DESKTOP_QUERY]: false });
});

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
  matchMedia.restore();
});

it("opens as a compact modal with an inert hidden trigger", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  const trigger = screen.getByRole("button", { name: "追问这份日报" });

  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveAttribute("aria-controls", "report-chat-report-1");
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "追问这份日报" });
  expect(dialog).toHaveAttribute("id", "report-chat-report-1");
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(screen.getByTestId("report-chat-backdrop")).toHaveClass("fixed", "inset-0");
  expect(document.body.style.overflow).toBe("hidden");
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(trigger).toHaveAttribute("tabindex", "-1");
  expect(trigger).toHaveClass("invisible", "pointer-events-none");
});

it("closes on Escape, restores focus, and reveals the trigger", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  const trigger = screen.getByRole("button", { name: "追问这份日报" });
  fireEvent.click(trigger);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).not.toHaveAttribute("tabindex");
  expect(trigger).not.toHaveClass("invisible", "pointer-events-none");
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
  fireEvent.click(screen.getByTestId("report-chat-backdrop"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("preserves the previous body overflow and wraps compact keyboard focus", () => {
  document.body.style.overflow = "clip";
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  const trigger = screen.getByRole("button", { name: "追问这份日报" });
  fireEvent.click(trigger);
  const close = screen.getByRole("button", { name: "关闭追问助手" });
  expect(close).toHaveFocus();

  trigger.focus();
  fireEvent.keyDown(document, { key: "Tab" });
  expect(close).toHaveFocus();

  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  expect(screen.getByRole("textbox", { name: "聊天输入" })).toHaveFocus();

  fireEvent.click(close);
  expect(document.body.style.overflow).toBe("clip");
});

it("opens as a desktop complementary rail without modal side effects", () => {
  document.body.style.overflow = "clip";
  act(() => matchMedia.setMatches(DESKTOP_QUERY, true));
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));

  const complementary = screen.getByRole("complementary", { name: "追问这份日报" });
  const close = screen.getByRole("button", { name: "关闭追问助手" });
  const input = screen.getByRole("textbox", { name: "聊天输入" });
  expect(complementary).not.toHaveAttribute("aria-modal");
  expect(screen.queryByTestId("report-chat-backdrop")).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe("clip");

  close.focus();
  expect(fireEvent.keyDown(document, { key: "Tab", shiftKey: true })).toBe(true);
  expect(close).toHaveFocus();
  expect(input).not.toHaveFocus();

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "追问这份日报" })).toHaveFocus();
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
