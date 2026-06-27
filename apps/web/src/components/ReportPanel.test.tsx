// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { Report } from "@/lib/api";
import type { StreamingReportState } from "@/lib/streamApi";
import { ReportPanel } from "@/components/ReportPanel";

afterEach(() => {
  cleanup();
});

function streamingState(overrides: Partial<StreamingReportState> = {}): StreamingReportState {
  return {
    stage: "generating",
    stageLabel: "AI 分析中（流式）…",
    fundCodes: ["519674", "015945"],
    fundNames: ["银河创新成长", "易方达国防军工"],
    partialByCode: {},
    stageLog: [
      { stage: "fund_data", label: "正在拉取净值…", at: Date.now() - 5000 },
      { stage: "generating", label: "AI 分析中…", at: Date.now() },
    ],
    thinkingNotes: [],
    startedAt: Date.now() - 5000,
    tokenBuffer: "",
    followupNotes: [],
    ...overrides,
  };
}

function sampleReport(): Report {
  return {
    id: "report-1",
    created_at: "2026-06-25T10:00:00Z",
    title: "持仓盘点日报",
    summary: "今日观望为主。",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 1.2,
      alerts: [],
    },
    holdings: [
      {
        fund_code: "519674",
        fund_name: "银河创新成长",
        sector_name: "半导体",
        holding_amount: 10000,
        return_percent: 1.2,
      },
    ],
    snapshots: [
      {
        fund_code: "519674",
        fund_name: "银河创新成长",
        source: "test",
      },
    ],
    market_context: [],
    market_news: [],
    topic_briefs: [],
    fund_recommendations: [
      {
        fund_code: "519674",
        fund_name: "银河创新成长",
        action: "观察",
        points: ["估值偏高，暂不加仓"],
      },
    ],
    recommendations: ["组合整体观望"],
    caveats: ["仅供参考"],
    provider: "deepseek-v4-flash",
  };
}

describe("ReportPanel streaming", () => {
  it("shows stage label while streaming", () => {
    render(<ReportPanel report={null} streaming={streamingState()} />);
    expect(screen.getByTestId("report-streaming")).toBeInTheDocument();
    expect(screen.getByText("AI 分析中（流式）…")).toBeInTheDocument();
    expect(screen.getByTestId("report-thinking-sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("stage-card-generating")).toHaveAttribute("data-status", "active");
  });

  it("renders one skeleton card per fund code", () => {
    render(<ReportPanel report={null} streaming={streamingState()} />);
    expect(screen.getByTestId("report-skeleton-519674")).toBeInTheDocument();
    expect(screen.getByTestId("report-skeleton-015945")).toBeInTheDocument();
    expect(screen.getByText(/正在分析 银河创新成长/)).toBeInTheDocument();
  });

  it("keeps a visible frame after falling back before skeleton data arrives", () => {
    render(
      <ReportPanel
        report={null}
        streaming={streamingState({
          fundCodes: [],
          fundNames: [],
          backgroundJobId: "job-1",
          backgroundFallbackReason: "stopped at news summary",
        })}
      />,
    );
    expect(screen.getByTestId("report-background-fallback-frame")).toBeInTheDocument();
  });

  it("renders partial fund recommendation when patch arrives", () => {
    render(
      <ReportPanel
        report={null}
        streaming={streamingState({
          partialByCode: {
            "519674": {
              fund_code: "519674",
              fund_name: "银河创新成长",
              action: "观察",
              points: ["等待回调"],
            },
          },
        })}
      />,
    );
    expect(screen.getByTestId("report-partial-519674")).toBeInTheDocument();
    expect(screen.getByText("观察")).toBeInTheDocument();
    expect(screen.getByText("等待回调")).toBeInTheDocument();
    expect(screen.getByTestId("report-skeleton-015945")).toBeInTheDocument();
  });

  it("shows title and summary from partial stream", () => {
    render(
      <ReportPanel
        report={null}
        streaming={streamingState({
          title: "流式标题",
          summary: "流式摘要",
        })}
      />,
    );
    expect(screen.getByText("流式标题")).toBeInTheDocument();
    expect(screen.getByText("流式摘要")).toBeInTheDocument();
  });

  it("shows token typewriter preview while generating", () => {
    render(
      <ReportPanel
        report={null}
        streaming={streamingState({
          stage: "generating",
          tokenBuffer: '{"title":"持仓盘点","summary":"今日',
        })}
      />,
    );
    expect(screen.getByTestId("stream-token-preview")).toBeInTheDocument();
    expect(screen.getByText(/持仓盘点/)).toBeInTheDocument();
  });
});

describe("ReportPanel done", () => {
  it("renders full report view after stream completes", () => {
    render(<ReportPanel report={sampleReport()} streaming={null} />);
    expect(screen.getByTestId("report-ready")).toBeInTheDocument();
    expect(screen.getByText("持仓盘点日报")).toBeInTheDocument();
    expect(screen.getByText("今日观望为主。")).toBeInTheDocument();
    expect(screen.getByText(/519674 · 银河创新成长/)).toBeInTheDocument();
  });
});
