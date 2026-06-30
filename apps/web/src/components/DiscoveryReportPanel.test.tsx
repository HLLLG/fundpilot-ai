// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { FundDiscoveryReport } from "@/lib/api";
import { DiscoveryReportPanel } from "@/components/DiscoveryReportPanel";

vi.mock("@/components/DiscoveryOutcomesPanel", () => ({
  DiscoveryOutcomesPanel: () => <div data-testid="outcomes-panel" />,
}));

vi.mock("@/components/DiscoveryChatPanel", () => ({
  DiscoveryChatPanel: () => <div data-testid="chat-panel" />,
}));

afterEach(() => {
  cleanup();
});

function sampleReport(): FundDiscoveryReport {
  return {
    id: "disc-1",
    created_at: "2026-06-30T10:00:00Z",
    title: "全市场机会扫描",
    summary: "电子方向相对更稳，半导体材料等待回调。",
    market_view: "顺势方向需控制追高。",
    focus_sectors: [],
    target_sectors: ["电子", "半导体材料"],
    discovery_facts: {
      sector_opportunities: [
        {
          sector_label: "电子",
          track: "momentum",
          score: 66.46,
          confidence: "低",
          entry_hint: "高位谨慎",
          today_main_force_net_yi: -195.82,
          cumulative_5d_net_yi: -815.11,
          pattern_label: "distribution",
        },
      ],
    },
    candidate_pool: [
      {
        fund_code: "006081",
        fund_name: "海富通电子传媒股票A",
        sector_label: "电子",
        fund_quality_score: 53.37,
        sector_fit_score: 37.12,
        quality_reasons: ["板块高置信匹配"],
        quality_penalties: ["缺少基金规模"],
        return_3m_percent: 90.79,
        return_6m_percent: 91.1,
        return_1y_percent: 262.13,
      },
    ],
    recommendations: [
      {
        fund_code: "006081",
        fund_name: "海富通电子传媒股票A",
        sector_name: "电子",
        action: "建议关注",
        hold_horizon: "2-4周",
        confidence: "中",
        decision_path: "先判断电子板块方向，再比较基金质量，系统校验后最终动作调整为建议关注。",
        sector_evidence: ["机会分 66.46，track=momentum", "今日主力净流出 195.82 亿"],
        fund_evidence: ["fund_quality_score=53.37，sector_fit_score=37.12"],
        validation_notes: ["缺少基金规模"],
        points: ["质量分适中，等待回调"],
        risks: ["短线波动较高"],
        news_bullish: [],
      },
    ],
    caveats: ["仅供参考"],
    provider: "deepseek",
    analysis_mode: "fast",
  };
}

describe("DiscoveryReportPanel", () => {
  it("renders sector opportunities and structured recommendation evidence", () => {
    render(<DiscoveryReportPanel report={sampleReport()} />);

    expect(screen.getByText("本次主方向")).toBeInTheDocument();
    expect(screen.getByText("电子")).toBeInTheDocument();
    expect(screen.getByText(/机会分 66.46/)).toBeInTheDocument();
    expect(screen.getAllByText(/track=momentum/).length).toBeGreaterThan(0);
    expect(screen.getByText("决策路径")).toBeInTheDocument();
    expect(screen.getByText(/系统校验后最终动作调整为建议关注/)).toBeInTheDocument();
    expect(screen.getByText("板块依据")).toBeInTheDocument();
    expect(screen.getByText("基金依据")).toBeInTheDocument();
    expect(screen.getByText("校验备注")).toBeInTheDocument();
    expect(screen.getByText(/fund_quality_score=53.37/)).toBeInTheDocument();
  });

  it("shows candidate quality columns when the pool is expanded", () => {
    render(<DiscoveryReportPanel report={sampleReport()} />);

    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    expect(screen.getByText("质量分")).toBeInTheDocument();
    expect(screen.getByText("匹配分")).toBeInTheDocument();
    expect(screen.getByText("53.37")).toBeInTheDocument();
    expect(screen.getByText("37.12")).toBeInTheDocument();
    expect(screen.getByText(/板块高置信匹配/)).toBeInTheDocument();
    expect(screen.getAllByText(/缺少基金规模/).length).toBeGreaterThan(0);
  });
});
