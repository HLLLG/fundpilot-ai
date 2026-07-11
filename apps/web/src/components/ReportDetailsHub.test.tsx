// @vitest-environment jsdom

import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportDetailsHub } from "@/components/ReportDetailsHub";
import type { Report } from "@/lib/api";

vi.mock("@/components/ReportNewsBriefPanel", () => ({
  ReportNewsBriefPanel: () => <div data-testid="news-panel" />,
}));

vi.mock("@/components/RebalanceSimulationPanel", () => ({
  RebalanceSimulationPanel: () => <div data-testid="rebalance-panel" />,
}));

vi.mock("@/components/ReportOutcomesPanel", () => ({
  ReportOutcomesPanel: () => <div data-testid="outcomes-panel" />,
}));

afterEach(() => {
  cleanup();
});

function sampleReport(): Report {
  return {
    id: "report-1",
    created_at: "2026-07-11T10:00:00Z",
    title: "测试日报",
    summary: "测试摘要",
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
    topic_briefs: [
      {
        topic: "人工智能",
        summary: "主题摘要",
        points: [],
        news_count: 1,
        provider: "test",
      },
    ],
    fund_recommendations: [],
    recommendations: [],
    caveats: [],
    provider: "test",
    analysis_facts: {
      sector_rotation: {
        available: true,
        market_top: [{ sector_label: "医药", confidence: "中", score: 60 }],
      },
    },
  };
}

function props(): ComponentProps<typeof ReportDetailsHub> {
  return {
    report: sampleReport(),
    diagnostics: () => <div data-testid="diagnostics-content">诊断内容</div>,
  };
}

it("shows four compact entries without mounting tool content", () => {
  render(<ReportDetailsHub {...props()} />);

  for (const name of [
    "主题要闻摘要",
    "板块轮动参考",
    "调仓示意模拟",
    "建议复盘与投研诊断",
  ]) {
    const entry = screen.getByRole("button", { name });
    expect(entry).toHaveAttribute("aria-expanded", "false");
    expect(entry).toHaveAttribute("aria-controls");
    expect(entry).toHaveClass("min-h-11");
  }

  expect(screen.queryByTestId("news-panel")).not.toBeInTheDocument();
  expect(screen.queryByTestId("rebalance-panel")).not.toBeInTheDocument();
  expect(screen.queryByTestId("outcomes-panel")).not.toBeInTheDocument();
  expect(screen.queryByTestId("diagnostics-content")).not.toBeInTheDocument();
});

it("mounts only the selected detail panel", () => {
  render(<ReportDetailsHub {...props()} />);

  fireEvent.click(screen.getByRole("button", { name: "建议复盘与投研诊断" }));

  expect(screen.getByTestId("outcomes-panel")).toBeInTheDocument();
  expect(screen.getByTestId("diagnostics-content")).toBeInTheDocument();
  expect(screen.queryByTestId("news-content")).not.toBeInTheDocument();
  expect(screen.queryByTestId("rebalance-panel")).not.toBeInTheDocument();
});

it("only offers data-backed news and rotation entries", () => {
  const report = sampleReport();
  report.topic_briefs = [];
  report.market_news = [];
  report.analysis_facts = undefined;

  render(<ReportDetailsHub report={report} />);

  expect(screen.queryByRole("button", { name: "主题要闻摘要" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "板块轮动参考" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "调仓示意模拟" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "建议复盘与投研诊断" })).toBeInTheDocument();
});

it("collapses the active panel when its entry is pressed again", () => {
  render(<ReportDetailsHub {...props()} />);
  const newsEntry = screen.getByRole("button", { name: "主题要闻摘要" });

  fireEvent.click(newsEntry);
  expect(newsEntry).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByTestId("news-panel")).toBeInTheDocument();

  fireEvent.click(newsEntry);
  expect(newsEntry).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByTestId("news-panel")).not.toBeInTheDocument();
});
