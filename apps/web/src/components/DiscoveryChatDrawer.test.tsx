// @vitest-environment jsdom

import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { DiscoveryChatDrawer } from "@/components/DiscoveryChatDrawer";

vi.mock("@/components/DiscoveryChatPanel", () => ({
  DiscoveryChatPanel: () => (
    <div data-testid="chat-panel">
      <button type="button">聊天内部操作</button>
    </div>
  ),
}));

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

function DrawerHarness() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开追问
      </button>
      <DiscoveryChatDrawer
        id="discovery-chat-test"
        open={open}
        onClose={() => setOpen(false)}
        reportId="disc-1"
        reportTitle="全市场机会扫描"
      />
    </>
  );
}

describe("DiscoveryChatDrawer", () => {
  it("mounts chat only while open and restores focus after Escape", () => {
    render(<DrawerHarness />);

    const trigger = screen.getByRole("button", { name: "打开追问" });
    trigger.focus();
    expect(screen.queryByTestId("chat-panel")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "追问本次推荐" });
    const closeButton = screen.getByRole("button", { name: "关闭追问面板" });
    expect(dialog).toHaveAttribute("id", "discovery-chat-test");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveClass("inset-0", "sm:max-w-md");
    expect(screen.getByText("全市场机会扫描")).toBeInTheDocument();
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    expect(closeButton).toHaveFocus();
    expect(closeButton).toHaveClass("h-11", "w-11");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chat-panel")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
    expect(trigger).toHaveFocus();
  });

  it("closes only when the backdrop itself is clicked", () => {
    render(<DrawerHarness />);
    const trigger = screen.getByRole("button", { name: "打开追问" });
    trigger.focus();
    fireEvent.click(trigger);

    fireEvent.click(screen.getByTestId("discovery-chat-drawer"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("discovery-chat-backdrop"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("loops keyboard focus within the drawer", () => {
    render(<DrawerHarness />);
    fireEvent.click(screen.getByRole("button", { name: "打开追问" }));

    const closeButton = screen.getByRole("button", { name: "关闭追问面板" });
    const innerButton = screen.getByRole("button", { name: "聊天内部操作" });

    innerButton.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(closeButton).toHaveFocus();

    closeButton.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(innerButton).toHaveFocus();
  });
});
