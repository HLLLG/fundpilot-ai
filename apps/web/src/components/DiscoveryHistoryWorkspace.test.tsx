// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { DiscoveryHistoryWorkspace } from "@/components/DiscoveryHistoryWorkspace";
import type { FundDiscoveryReport } from "@/lib/api";

vi.mock("@/lib/api", () => ({ deleteDiscoveryReport: vi.fn() }));

function makeReports(count: number): FundDiscoveryReport[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `discovery-${index + 1}`,
    title: `推荐 ${String(index + 1).padStart(3, "0")}`,
    created_at: new Date(Date.UTC(2026, 6, 12, 8, 0) - index * 86_400_000).toISOString(),
    summary: "测试摘要",
    focus_sectors: [],
    target_sectors: [],
    recommendations: [],
    caveats: [],
    provider: "test",
  }));
}

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

it("keeps history out of the reading area until the drawer is requested", () => {
  const reports = makeReports(100);
  const view = render(
    <DiscoveryHistoryWorkspace
      reports={reports}
      activeReportId="discovery-80"
      open={false}
      onClose={vi.fn()}
      onRefresh={vi.fn()}
      onSelect={vi.fn()}
    />,
  );

  expect(screen.queryByRole("dialog", { name: "历史推荐" })).not.toBeInTheDocument();
  expect(screen.queryByTestId("discovery-history-item")).not.toBeInTheDocument();

  view.rerender(
    <DiscoveryHistoryWorkspace
      reports={reports}
      activeReportId="discovery-80"
      open
      onClose={vi.fn()}
      onRefresh={vi.fn()}
      onSelect={vi.fn()}
    />,
  );

  expect(screen.getByRole("dialog", { name: "历史推荐" })).toBeInTheDocument();
  expect(screen.getAllByTestId("discovery-history-item")).toHaveLength(21);
  expect(screen.getByTestId("discovery-history-scroll-region")).toHaveClass(
    "history-scroll-region",
  );
  expect(screen.getByText("推荐 080").closest("button")).toHaveAttribute(
    "aria-current",
    "true",
  );
});

it("opens a focus-managed drawer, selects in context, closes and restores focus", () => {
  const reports = makeReports(100);
  const onSelect = vi.fn();

  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>打开历史推荐</button>
        <DiscoveryHistoryWorkspace
          reports={reports}
          activeReportId="discovery-1"
          open={open}
          onClose={() => setOpen(false)}
          onRefresh={vi.fn()}
          onSelect={onSelect}
        />
      </>
    );
  }

  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "打开历史推荐" });
  trigger.focus();
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "历史推荐" });
  expect(within(dialog).getByRole("button", { name: "返回并关闭历史推荐" })).toHaveFocus();
  expect(within(dialog).getAllByTestId("discovery-history-item")).toHaveLength(20);
  fireEvent.click(within(dialog).getByText("推荐 002").closest("button")!);

  expect(onSelect).toHaveBeenCalledWith(reports[1], "drawer");
  expect(screen.queryByRole("dialog", { name: "历史推荐" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
