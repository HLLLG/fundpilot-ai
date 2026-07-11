// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
      {
        fund_code: "015945",
        fund_name: "易方达国防军工",
        action: "分批加仓",
        points: ["资金面改善，可小额分批"],
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
  it("keeps the floating chat trigger outside transformed report content", () => {
    render(<ReportPanel report={sampleReport()} streaming={null} />);

    const chatTrigger = screen.getByRole("button", { name: "追问这份日报" });
    expect(chatTrigger.closest(".animate-fade-up")).toBeNull();
  });

  it("renders full report view after stream completes", () => {
    render(<ReportPanel report={sampleReport()} streaming={null} />);
    expect(screen.getByTestId("report-ready")).toBeInTheDocument();
    expect(screen.getByText("持仓盘点日报")).toBeInTheDocument();
    expect(screen.getByText("今日观望为主。")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "展开 银河创新成长" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "需要处理" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "继续观察" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "追问这份日报" })).toBeInTheDocument();
    expect(screen.queryByTestId("report-chat-panel")).not.toBeInTheDocument();
    expect(
      screen.queryByText("逐基金操作建议与依据；宽屏时右侧可追问，窄屏时追问在下方。"),
    ).not.toBeInTheDocument();
  });

  it("renders structured decision fields and sector direction context when present", () => {
    const report = sampleReport();
    report.fund_recommendations = [
      {
        fund_code: "519674",
        fund_name: "银河创新成长",
        action: "分批加仓",
        confidence: "高",
        hold_horizon: "1-2周",
        decision_path: "先看板块方向，再看基金证据，最后给出动作",
        sector_evidence: ["顺势观察，置信度高"],
        fund_evidence: ["三路量化证据综合置信：高"],
        validation_notes: ["样本有限"],
        risks: ["板块波动可能导致净值回撤"],
        points: ["测试要点"],
      },
    ];
    report.analysis_facts = {
      holdings: [
        {
          fund_code: "519674",
          sector_opportunity: {
            sector_label: "半导体",
            track: "momentum",
            confidence: "高",
            entry_hint: "资金进入，可少量参与",
            opportunity_available: true,
          },
        },
      ],
      sector_rotation: {
        available: true,
        market_top: [
          {
            sector_label: "医药",
            track: "setup",
            confidence: "中",
            score: 57.2,
          },
        ],
      },
    };

    render(<ReportPanel report={report} streaming={null} />);

    expect(screen.getByText("参考度：高")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起 银河创新成长" })).toBeInTheDocument();
    expect(screen.getByText(/主要风险：板块波动可能导致净值回撤/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "专业依据" }));
    expect(screen.getByText("半导体")).toBeInTheDocument();
    expect(screen.getByText("资金进入，可少量参与")).toBeInTheDocument();
    expect(screen.getByText("先看板块方向，再看基金证据，最后给出动作")).toBeInTheDocument();
    expect(screen.getByText("板块依据")).toBeInTheDocument();
    expect(screen.getByText("基金依据")).toBeInTheDocument();
    expect(screen.getByText("校验备注")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /板块轮动参考/ }));
    expect(screen.getByText("医药")).toBeInTheDocument();
  });
});
