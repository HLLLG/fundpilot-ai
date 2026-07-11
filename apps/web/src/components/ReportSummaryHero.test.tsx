// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportSummaryHero } from "@/components/ReportSummaryHero";
import type { Report } from "@/lib/api";

function sampleReport(): Report {
  return {
    id: "report-1",
    created_at: "2026-07-11T10:00:00Z",
    title: "持仓盘点日报",
    summary: "今日观望为主。",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 3.71,
      alerts: [],
    },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    topic_briefs: [],
    fund_recommendations: [],
    recommendations: ["组合整体保持观察"],
    caveats: [],
    provider: "deepseek-v4-pro",
  };
}

afterEach(() => cleanup());

it("shows the action-first conclusion and keeps details behind separate disclosures", () => {
  const onExport = vi.fn();

  render(
    <ReportSummaryHero
      report={sampleReport()}
      needsActionCount={1}
      isExporting={false}
      onExport={onExport}
    />,
  );

  expect(screen.getByRole("heading", { name: "持仓盘点日报" })).toBeInTheDocument();
  expect(screen.getByText("今日观望为主。")).toBeInTheDocument();
  expect(screen.getByText("风险 中等")).toBeInTheDocument();
  expect(screen.getByText("观察")).toBeInTheDocument();
  expect(screen.getByText("3.71%")).toBeInTheDocument();
  expect(screen.getByText("中等", { selector: "dd" })).toBeInTheDocument();
  expect(screen.getByText("1 只")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "导出 Markdown" }));
  expect(onExport).toHaveBeenCalledOnce();

  expect(screen.queryByText("deepseek-v4-pro")).not.toBeInTheDocument();
  expect(screen.queryByText("2026-07-11T10:00:00Z")).not.toBeInTheDocument();
  expect(screen.queryByText("组合整体保持观察")).not.toBeInTheDocument();

  const metadataTrigger = screen.getByRole("button", { name: "报告信息" });
  expect(metadataTrigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(metadataTrigger);
  expect(metadataTrigger).toHaveAttribute("aria-expanded", "true");
  const metadata = screen.getByTestId("report-summary-metadata");
  expect(within(metadata).getByText("deepseek-v4-pro")).toBeInTheDocument();
  expect(within(metadata).getByText("2026-07-11T10:00:00Z")).toBeInTheDocument();
  expect(within(metadata).queryByText("组合整体保持观察")).not.toBeInTheDocument();

  const portfolioTrigger = screen.getByRole("button", { name: "组合说明" });
  expect(portfolioTrigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(portfolioTrigger);
  expect(portfolioTrigger).toHaveAttribute("aria-expanded", "true");
  const portfolio = screen.getByTestId("report-summary-portfolio");
  expect(within(portfolio).getByText("组合整体保持观察")).toBeInTheDocument();
  expect(within(portfolio).queryByText("deepseek-v4-pro")).not.toBeInTheDocument();
});

it("exposes an accessible disabled state while Markdown is exporting", () => {
  const onExport = vi.fn();

  render(
    <ReportSummaryHero
      report={sampleReport()}
      needsActionCount={1}
      isExporting
      onExport={onExport}
    />,
  );

  const exportButton = screen.getByRole("button", { name: "正在导出 Markdown" });
  expect(exportButton).toBeDisabled();
  expect(exportButton).toHaveAttribute("aria-busy", "true");
  fireEvent.click(exportButton);
  expect(onExport).not.toHaveBeenCalled();
});

it("keeps all three KPI tiles in a shrinkable mobile row", () => {
  render(
    <ReportSummaryHero
      report={sampleReport()}
      needsActionCount={12}
      isExporting={false}
      onExport={vi.fn()}
    />,
  );

  const metrics = screen.getByTestId("report-summary-metrics");
  expect(metrics).toHaveClass("grid-cols-3", "min-w-0");
  expect(within(metrics).getAllByRole("term")).toHaveLength(3);
  for (const tile of within(metrics).getAllByRole("term").map((term) => term.parentElement)) {
    expect(tile).toHaveClass("min-w-0");
  }
});

it("keeps summary actions at least 44px tall on touch screens", () => {
  render(
    <ReportSummaryHero
      report={sampleReport()}
      needsActionCount={1}
      isExporting={false}
      onExport={vi.fn()}
    />,
  );

  for (const name of ["组合说明", "报告信息", "导出 Markdown"]) {
    expect(screen.getByRole("button", { name })).toHaveClass("min-h-11");
  }
});

it("omits the portfolio disclosure when no portfolio-level recommendation exists", () => {
  const report = sampleReport();
  report.recommendations = ["[000001 · 观察] 继续跟踪"];

  render(
    <ReportSummaryHero
      report={report}
      needsActionCount={0}
      isExporting={false}
      onExport={vi.fn()}
    />,
  );

  expect(screen.queryByRole("button", { name: "组合说明" })).not.toBeInTheDocument();
});
