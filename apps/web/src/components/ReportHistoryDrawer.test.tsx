// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportHistoryDrawer } from "@/components/ReportHistoryDrawer";
import type { Report } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
  deleteReport: vi.fn(),
  fetchSectorSignalBacktest: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  deleteReport: apiMocks.deleteReport,
  fetchSectorSignalBacktest: apiMocks.fetchSectorSignalBacktest,
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

beforeEach(() => {
  apiMocks.fetchSectorSignalBacktest.mockResolvedValue({
    enabled: false,
    message: "回测已按需加载",
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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

it("requests the sector backtest only while its disclosure is expanded", async () => {
  render(
    <ReportHistoryDrawer
      open
      reports={[report("r1", "日报甲")]}
      activeReportId="r1"
      onClose={vi.fn()}
      onRefresh={vi.fn()}
      onSelect={vi.fn()}
      onDeleted={vi.fn()}
    />,
  );

  const summary = screen.getByText("研究分析与板块回测");
  const disclosure = summary.closest("details");
  expect(disclosure).not.toHaveAttribute("open");
  expect(apiMocks.fetchSectorSignalBacktest).not.toHaveBeenCalled();
  expect(screen.queryByText("回测已按需加载")).not.toBeInTheDocument();

  fireEvent.click(summary);

  await waitFor(() => expect(disclosure).toHaveAttribute("open"));
  await waitFor(() => expect(apiMocks.fetchSectorSignalBacktest).toHaveBeenCalledOnce());
  expect(apiMocks.fetchSectorSignalBacktest).toHaveBeenCalledWith(120, undefined);
  expect(await screen.findByText("回测已按需加载")).toBeInTheDocument();

  fireEvent.click(summary);

  await waitFor(() => expect(disclosure).not.toHaveAttribute("open"));
  expect(screen.queryByText("回测已按需加载")).not.toBeInTheDocument();
  expect(apiMocks.fetchSectorSignalBacktest).toHaveBeenCalledOnce();
});
