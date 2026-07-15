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

  it("shows the conservative cost upper bound with execution evidence", () => {
    const report = sampleReport();
    report.recommendations[0] = {
      ...report.recommendations[0],
      tradeability: {
        data_status: "complete",
        freshness: "fresh",
        purchase_state: "limited",
        purchase_status: "限制大额申购",
        redemption_state: "open",
        redemption_status: "开放赎回",
        minimum_initial_purchase_yuan: 10,
        minimum_additional_purchase_yuan: 1,
        explicit_minimum_holding_days: 14,
        daily_purchase_limit_yuan: 1000,
        daily_purchase_limit_unlimited: false,
        revalidation_required: true,
        source_ids: ["eastmoney.fundf10_purchase_info"],
        checked_at: "2026-07-14T09:59:30+08:00",
        tradeability_gate: {
          status: "eligible",
          effective_initial_min_purchase_yuan: 100,
          max_purchase_yuan: 1000,
          max_purchase_unlimited: false,
          revalidation_required: true,
        },
      },
      cost_assessment: {
        executable: true,
        minimum_holding_days: 14,
        minimum_purchase_yuan: 100,
        daily_purchase_limit_yuan: 1000,
        estimated_total_cost_upper_bound_percent: 1.2345,
        fee_status: "standard_upper_bound_available",
        source_ids: ["eastmoney.fundf10_purchase_info"],
        checked_at: "2026-07-14T09:59:30+08:00",
      },
    };

    render(<DiscoveryReportPanel report={report} />);

    const evidence = screen.getByRole("region", { name: "交易条件与成本核验" });
    expect(evidence).toHaveTextContent("申购限大额");
    expect(evidence).toHaveTextContent("赎回开放");
    expect(evidence).toHaveTextContent("首次起购");
    expect(evidence).toHaveTextContent("¥10");
    expect(evidence).toHaveTextContent("本台执行门槛 ¥100");
    expect(evidence).toHaveTextContent("追加起购");
    expect(evidence).toHaveTextContent("¥1");
    expect(evidence).toHaveTextContent("单日申购限额");
    expect(evidence).toHaveTextContent("¥1,000");
    expect(evidence).toHaveTextContent("最低持有期 14 天");
    expect(evidence).toHaveTextContent("未折扣标准费率成本上限");
    expect(evidence).toHaveTextContent("约 1.23%");
    expect(evidence).toHaveTextContent("按最短 14 天");
    expect(evidence).toHaveTextContent("下单前复核");
    expect(evidence).toHaveTextContent("不代表销售平台最终成交费");
  });

  it("does not present a buy event as executable when the tradeability gate fails", () => {
    const report = sampleReport();
    const recommendation = {
      ...report.recommendations[0],
      action: "分批买入",
      tradeability_gate: {
        status: "watch_only" as const,
        reason_codes: ["purchase_not_open"],
      },
    };
    report.discovery_facts = {
      ...report.discovery_facts,
      data_evidence_guard: {
        execution_blocked: false,
        blocked_fund_codes: [],
      },
    };
    report.recommendations = [recommendation];
    report.decision_events = [
      {
        fund_code: recommendation.fund_code,
        action_category: "buy",
        eligible: true,
      },
    ];

    render(<DiscoveryReportPanel report={report} />);

    expect(screen.getByText("本次暂无可执行建议")).toBeInTheDocument();
    expect(screen.getByText("研究观察")).toBeInTheDocument();
    expect(screen.queryByText("可执行建议")).not.toBeInTheDocument();
  });

  it("shows the verified current tranche separately from an amount-free future tranche", () => {
    const report = sampleReport();
    const recommendation = {
      ...report.recommendations[0],
      action: "分批买入",
      suggested_amount_yuan: 6300,
      amount_note: "当前首批由确定性分配器统一计算。",
      allocation: {
        fund_code: "006081",
        sector_name: "电子",
        suggested_amount_yuan: 6300,
        amount_semantics: "current_verified_initial_tranche" as const,
        future_tranches: [
          {
            sequence: 2,
            amount_yuan: null,
            revalidation_required: true,
            preconditions: [
              "tradeability_gate_recheck",
              "confirmed_cash_recheck",
              "risk_context_recheck",
            ],
          },
        ],
        revalidation_required: true,
      },
    };
    report.discovery_facts = {
      ...report.discovery_facts,
      data_evidence_guard: {
        execution_blocked: false,
        blocked_fund_codes: [],
      },
      risk_context: {
        schema_version: "discovery_risk_context.v1",
        status: "qualified",
        qualified: true,
        candidate_common_return_sample_days: 118,
        current_holdings_nav_amount_coverage_percent: 92.5,
      },
    };
    report.recommendations = [recommendation];
    report.decision_events = [
      { fund_code: "006081", action_category: "buy", eligible: true },
    ];
    report.allocation_plan = {
      schema_version: "discovery_allocation_plan.v1",
      status: "partial",
      amount_semantics: "current_verified_initial_tranche",
      risk_context: {
        schema_version: "discovery_risk_context.v1",
        status: "qualified",
        reason_codes: [],
      },
      budget: {
        requested_yuan: 50000,
        confirmed_cash_yuan: 40000,
        spendable_yuan: 40000,
        current_tranche_cap_yuan: 12500,
        allocated_current_tranche_yuan: 6300,
      },
      allocations: [recommendation.allocation],
      unallocated_budget: {
        amount_yuan: 43700,
        current_tranche_unallocated_yuan: 6200,
        deferred_future_tranches_yuan: 37500,
        unavailable_due_to_cash_yuan: 10000,
        reason_codes: ["candidate_capacity_exhausted"],
      },
      revalidation_required: true,
    };

    render(<DiscoveryReportPanel report={report} />);

    expect(screen.getByText("1 只通过可执行校验")).toBeInTheDocument();
    const plan = screen.getByRole("region", { name: "确定性首批分配" });
    expect(plan).toHaveTextContent("总预算");
    expect(plan).toHaveTextContent("¥50,000");
    expect(plan).toHaveTextContent("当前首批上限");
    expect(plan).toHaveTextContent("¥12,500");
    expect(plan).toHaveTextContent("延期至后续批次");
    expect(plan).toHaveTextContent("¥37,500");
    expect(plan).toHaveTextContent("已确认现金");
    expect(plan).toHaveTextContent("¥40,000");
    expect(plan).toHaveTextContent("组合风险上下文已通过");
    expect(plan).toHaveTextContent("候选共同收益样本 118 日");
    expect(plan).toHaveTextContent("当前持仓净值金额覆盖 92.5%");
    expect(plan).toHaveTextContent("因现金不足或未确认不可用：¥10,000");

    const currentTranche = screen.getByLabelText("当前已验证首批金额");
    expect(currentTranche).toHaveTextContent("当前已验证首批");
    expect(currentTranche).toHaveTextContent("¥6,300");
    expect(screen.getByText("后续批次待重新核验 · 金额留空")).toBeInTheDocument();
    expect(screen.getByText(/交易条件、可用现金、板块敞口与组合风险需在执行前重新计算/)).toBeInTheDocument();
  });

  it("fails closed when a modern allocation plan has no verified allocation row", () => {
    const report = sampleReport();
    report.discovery_facts = {
      ...report.discovery_facts,
      data_evidence_guard: { execution_blocked: false, blocked_fund_codes: [] },
    };
    report.recommendations = [
      { ...report.recommendations[0], action: "分批买入", suggested_amount_yuan: 999999 },
    ];
    report.decision_events = [
      { fund_code: "006081", action_category: "buy", eligible: true },
    ];
    report.allocation_plan = {
      schema_version: "discovery_allocation_plan.v1",
      status: "blocked",
      amount_semantics: "current_verified_initial_tranche",
      allocations: [],
      risk_context: {
        schema_version: "discovery_risk_context.v1",
        status: "risk_context_unavailable",
      },
      budget: { requested_yuan: 50000, current_tranche_cap_yuan: 12500 },
      unallocated_budget: { amount_yuan: 50000 },
    };

    render(<DiscoveryReportPanel report={report} />);

    expect(screen.getByText("本次暂无可执行建议")).toBeInTheDocument();
    expect(screen.getByText("研究观察")).toBeInTheDocument();
    expect(screen.queryByText("可执行建议")).not.toBeInTheDocument();
    expect(screen.getByText("组合风险上下文未通过或未记录")).toBeInTheDocument();
    expect(screen.getByLabelText("历史参考金额")).toHaveTextContent("¥999,999");
  });

  it("keeps historical reports without allocation fields renderable", () => {
    const report = sampleReport();
    expect(report.allocation_plan).toBeUndefined();

    render(<DiscoveryReportPanel report={report} />);

    expect(screen.getByText(report.title)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "确定性首批分配" })).not.toBeInTheDocument();
    expect(screen.queryByText("当前已验证首批")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "基金持仓穿透证据" })).not.toBeInTheDocument();
  });

  it("maps discovery lookthrough candidates to the visible candidate pool names", () => {
    const report = sampleReport();
    report.discovery_facts = {
      ...report.discovery_facts,
      fund_lookthrough: {
        status: "qualified",
        decision_at: "2026-07-14T10:00:00+08:00",
        portfolio: {
          scope: "whole_account",
          identity_known_security_mass_lower_bound_percent: 48,
          unknown_account_mass_percent: 52,
        },
        candidates: {
          "006081": {
            portfolio_security_overlap_lower_bound_percent: null,
            common_disclosed_weight_percent: 0,
            portfolio_overlap_interpretation: "no_common_in_disclosed_scope",
            snapshot: {
              report_period: "2026-03-31",
              available_at: "2026-04-25T09:00:00+08:00",
            },
          },
        },
      },
    };

    render(<DiscoveryReportPanel report={report} />);

    const evidence = screen.getByRole("region", { name: "基金持仓穿透证据" });
    expect(evidence).toHaveTextContent("海富通电子传媒股票A");
    expect(evidence).toHaveTextContent("006081");
    expect(evidence).toHaveTextContent(
      "披露范围内未发现共同证券，完整组合重合未知",
    );
    expect(evidence).not.toHaveTextContent(/≥\s*0%/);
    expect(evidence).not.toHaveTextContent("完全分散");
  });
});
