// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { SectorMappingModal } from "@/components/SectorMappingModal";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开板块映射
      </button>
      <SectorMappingModal
        open={open}
        fundName="测试基金"
        sectorName="人工智能"
        candidates={[
          { source_type: "concept", source_name: "AI 应用", change_percent: 1.2 },
        ]}
        onClose={() => setOpen(false)}
        onSelect={vi.fn()}
      />
    </>
  );
}

function QueueHarness() {
  const [index, setIndex] = useState(0);
  const names = ["第一只基金", "第二只基金"];
  return (
    <SectorMappingModal
      open={index < names.length}
      fundName={names[index] ?? ""}
      sectorName="人工智能"
      candidates={[
        { source_type: "concept", source_name: `候选 ${index + 1}`, change_percent: 1.2 },
      ]}
      onClose={() => setIndex(names.length)}
      onSelect={() => setIndex((current) => current + 1)}
    />
  );
}

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

describe("SectorMappingModal", () => {
  it("traps focus, closes on Escape, and restores the trigger", () => {
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "打开板块映射" });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "选择板块映射" });
    const cancel = within(dialog).getByRole("button", { name: "稍后" });
    const candidate = within(dialog).getByRole("button", { name: /AI 应用/ });
    expect(cancel).toHaveFocus();
    expect(cancel).toHaveClass("min-h-11");
    expect(candidate).toHaveClass("min-h-11");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Tab" });
    expect(candidate).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "选择板块映射" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });

  it("moves focus safely when the mapping queue advances without closing", async () => {
    render(<QueueHarness />);
    await waitFor(() => expect(screen.getByRole("button", { name: "稍后" })).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: /候选 1/ }));
    expect(screen.getByText(/第二只基金/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "稍后" })).toHaveFocus());
  });
});
