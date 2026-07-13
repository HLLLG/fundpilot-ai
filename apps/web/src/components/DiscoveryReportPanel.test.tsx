// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { FundDiscoveryReport } from "@/lib/api";
import { DiscoveryReportPanel } from "@/components/DiscoveryReportPanel";

vi.mock("@/components/DiscoveryOutcomesPanel", () => ({
  DiscoveryOutcomesPanel: () => <div data-testid="outcomes-panel" />,
}));

vi.mock("@/components/DiscoveryChatDrawer", () => ({
  DiscoveryChatDrawer: ({ open }: { open: boolean }) =>
    open ? <div data-testid="chat-drawer" /> : null,
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
      data_evidence_guard: {
        execution_blocked: true,
        blocked_fund_codes: ["006081"],
        reasons_by_fund: { "006081": ["字段时点不可用"] },
      },
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
        decision_path:
          "电子方向在sector_opportunities中评分最高(66.46)，fund_quality_score=53.37，sector_fit_score=37.12，系统校验后最终动作调整为建议关注。",
        sector_evidence: ["机会分 66.46，track=momentum，confidence=低", "板块热度较高(heat_score 9.57)"],
        fund_evidence: ["fund_quality_score=53.37，sector_fit_score=37.12", "quality_reasons：板块高置信匹配"],
        validation_notes: [
          "quality_penalties提示缺少基金规模，max_drawdown_1y_percent为-69.8%",
          "nav_trend.distance_from_high_percent=0.0%，追高风险高",
        ],
        points: [
          "estimated_daily_return_percent 3.29%源自sector_estimate，为估算值",
          "当前净值处于区间高点(distance_from_high_percent 0.0)",
        ],
        risks: ["短线波动较高，nav_trend区间上升但需防回撤"],
        news_bullish: [],
      },
    ],
    decision_events: [
      {
        fund_code: "006081",
        final_action: "建议关注",
        action_category: "watch_only",
        eligible: false,
      },
    ],
    caveats: ["仅供参考"],
    provider: "deepseek",
    analysis_mode: "fast",
  };
}

describe("DiscoveryReportPanel", () => {
  it("keeps evidence-blocked recommendations in research observation instead of priority action", () => {
    render(<DiscoveryReportPanel report={sampleReport()} />);

    expect(screen.getByText("本次主方向")).toBeInTheDocument();
    expect(screen.getByText("电子")).toBeInTheDocument();
    expect(screen.getByText(/机会分 66.46/)).toBeInTheDocument();
    expect(screen.getAllByText(/顺势观察/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/track=momentum/)).not.toBeInTheDocument();
    expect(screen.queryByText(/pattern=/)).not.toBeInTheDocument();
    expect(screen.getByText("本次暂无可执行建议")).toBeInTheDocument();
    expect(screen.getByText("研究观察")).toBeInTheDocument();
    expect(screen.getByText(/其中 1 只被字段级证据守卫降为观察/)).toBeInTheDocument();
    expect(screen.queryByText("优先行动")).not.toBeInTheDocument();
    expect(screen.queryByText("可执行建议")).not.toBeInTheDocument();
    expect(screen.getByText(/核心理由/)).toBeInTheDocument();
    const evidenceDisclosure = screen.getByText("查看决策路径与专业依据");
    expect(screen.getByText("决策路径")).not.toBeVisible();
    fireEvent.click(evidenceDisclosure);
    expect(screen.getByText("决策路径")).toBeVisible();
    expect(screen.getByText(/系统校验后最终动作调整为建议关注/)).toBeVisible();
    expect(screen.getByText("板块依据")).toBeVisible();
    expect(screen.getByText("基金依据")).toBeVisible();
    expect(screen.getByText("校验备注")).toBeVisible();
    expect(screen.getAllByText(/基金质量分 53.37/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/fund_quality_score=/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/距离近期高点约 0.0%/).length).toBeGreaterThan(0);
    expect(screen.getByText(/系统筛出的主方向/)).toBeInTheDocument();
    expect(screen.getByText(/板块热度分 9.57/)).toBeInTheDocument();
    expect(screen.getByText(/系统校验提示/)).toBeInTheDocument();
    expect(screen.getByText(/近1年最大回撤约 69.8%/)).toBeInTheDocument();
    expect(screen.getByText(/今日涨跌约 3.29%/)).toBeInTheDocument();
    expect(screen.queryByText(/sector_opportunities/)).not.toBeInTheDocument();
    expect(screen.queryByText(/quality_penalties/)).not.toBeInTheDocument();
    expect(screen.queryByText(/max_drawdown_1y_percent/)).not.toBeInTheDocument();
    expect(screen.queryByText(/estimated_daily_return_percent/)).not.toBeInTheDocument();
    expect(screen.queryByText(/sector_estimate/)).not.toBeInTheDocument();
  });

  it("shows candidate quality columns when the pool is expanded", () => {
    render(<DiscoveryReportPanel report={sampleReport()} />);

    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    expect(screen.getAllByText("质量分").length).toBeGreaterThan(0);
    expect(screen.getAllByText("匹配分").length).toBeGreaterThan(0);
    expect(screen.getAllByText("53.37").length).toBeGreaterThan(0);
    expect(screen.getAllByText("37.12").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/板块高置信匹配/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/缺少基金规模/).length).toBeGreaterThan(0);
  });

  it("separates executable, conditional and observation decisions by structured events", () => {
    const report = sampleReport();
    const base = report.recommendations[0];
    report.discovery_facts = {
      ...report.discovery_facts,
      data_evidence_guard: {
        execution_blocked: true,
        blocked_fund_codes: ["000003"],
      },
    };
    report.recommendations = [
      { ...base, fund_code: "000001", fund_name: "可执行基金", action: "分批买入" },
      { ...base, fund_code: "000002", fund_name: "等待基金", action: "等待回调" },
      { ...base, fund_code: "000003", fund_name: "观察基金", action: "建议关注" },
    ];
    report.candidate_pool = report.recommendations.map((item) => ({
      fund_code: item.fund_code,
      fund_name: item.fund_name,
      sector_label: item.sector_name,
    }));
    report.decision_events = [
      { fund_code: "000001", action_category: "buy", eligible: true },
      { fund_code: "000002", action_category: "conditional_wait", eligible: false },
      { fund_code: "000003", action_category: "watch_only", eligible: false },
    ];

    render(<DiscoveryReportPanel report={report} />);

    expect(screen.getByText("1 只通过可执行校验")).toBeInTheDocument();
    expect(screen.getByText("可执行建议")).toBeInTheDocument();
    expect(screen.getByText("等待条件")).toBeInTheDocument();
    expect(screen.getByText("研究观察")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));
    expect(screen.getAllByText("可执行").length).toBeGreaterThan(0);
    expect(screen.getAllByText("等待条件").length).toBeGreaterThan(0);
    expect(screen.getAllByText("研究观察").length).toBeGreaterThan(0);
    expect(screen.queryByText("已推荐")).not.toBeInTheDocument();
  });

  it("never promotes a dip_swing research report into an executable recommendation", () => {
    const report = sampleReport();
    report.discovery_facts = {
      ...report.discovery_facts,
      data_evidence_guard: {
        execution_blocked: false,
        blocked_fund_codes: [],
      },
      effective_configuration: {
        scan_goal: "dip_swing",
        selection_policy: "dip_rebound_research",
      },
    };
    report.recommendations = [
      { ...report.recommendations[0], action: "分批买入", suggested_amount_yuan: 1000 },
    ];
    report.decision_events = [
      { fund_code: "006081", action_category: "buy", eligible: true },
    ];

    render(<DiscoveryReportPanel report={report} />);

    expect(screen.getByText("本次暂无可执行建议")).toBeInTheDocument();
    expect(screen.getByText("研究观察")).toBeInTheDocument();
    expect(screen.queryByText("可执行建议")).not.toBeInTheDocument();
  });

  it("does not mount the long follow-up chat until requested", () => {
    render(<DiscoveryReportPanel report={sampleReport()} />);

    expect(screen.queryByTestId("chat-drawer")).not.toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "追问本次推荐" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(trigger).toHaveAttribute("aria-controls", "discovery-report-chat-disc-1");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("chat-drawer")).toBeInTheDocument();
  });
});
