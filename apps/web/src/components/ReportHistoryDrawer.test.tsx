// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportHistoryDrawer } from "@/components/ReportHistoryDrawer";
import type { Report } from "@/lib/api";

vi.mock("@/lib/api", () => ({ deleteReport: vi.fn() }));
vi.mock("@/components/SectorSignalBacktestPanel", () => ({
  SectorSignalBacktestPanel: () => <div>回测内容</div>,
}));

function report(id: string, title: string): Report {
  return {
    id,
    title,
    created_at: "2026-07-11T08:00:00Z",
    risk: { level: "medium", suggested_action: "watch", weighted_return_percent: 0, alerts: [] },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    fund_recommendations: [],
    summary: "",
    recommendations: [],
    caveats: [],
    provider: "test",
  };
}

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

it("marks the current report and closes the drawer after selection", () => {
  const reports = [report("r1", "日报甲"), report("r2", "日报乙")];
  const onClose = vi.fn();
  const onSelect = vi.fn();
  render(
    <ReportHistoryDrawer
      open
      reports={reports}
      activeReportId="r1"
      onClose={onClose}
      onRefresh={vi.fn()}
      onSelect={onSelect}
      onDeleted={vi.fn()}
    />,
  );

  const dialog = screen.getByRole("dialog", { name: "全部历史日报" });
  expect(within(dialog).getByText("日报甲").closest("button")).toHaveAttribute(
    "aria-current",
    "true",
  );
  fireEvent.click(within(dialog).getByText("日报乙").closest("button")!);
  expect(onSelect).toHaveBeenCalledWith(reports[1]);
  expect(onClose).toHaveBeenCalledTimes(1);
});
