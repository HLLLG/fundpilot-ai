// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { Report } from "@/lib/api";
import { ReportPanel } from "@/components/ReportPanel";

// 这两个子面板挂载即发请求；本文件只验证结论与证据的渲染，替换成占位。
vi.mock("@/components/RebalanceSimulationPanel", () => ({
  RebalanceSimulationPanel: () => <div data-testid="rebalance-panel" />,
}));
vi.mock("@/components/ReportOutcomesPanel", () => ({
  ReportOutcomesPanel: () => <div data-testid="outcomes-panel" />,
}));
vi.mock("@/components/ReportChatDrawer", () => ({
  ReportChatDrawer: () => <div data-testid="chat-drawer" />,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function sampleReport(overrides: Partial<Report> = {}): Report {
  return {
    id: "rep-1",
    created_at: "2026-08-07T09:30:00Z",
    title: "每日基金操作日报",
    risk: {
      level: "high",
      suggested_action: "risk_review",
      weighted_return_percent: -16.4,
      alerts: [
        {
          code: "PORTFOLIO_COST_BASIS_LOSS",
          severity: "high",
          message: "组合相对持仓成本浮亏 16.40% 已触及 15.0% 成本浮亏复核线。",
          evidence: "按当前持仓金额加权的估算持有收益率。",
        },
        {
          code: "CONCENTRATION",
          severity: "medium",
          message: "银河创新成长 当前占比 45.0%，超过 30.0% 集中度上限。",
          evidence: "45000.00 / 100000.00（期望投入总额）",
        },
      ],
    },
    holdings: [
      {
        fund_code: "519674",
        fund_name: "银河创新成长",
        holding_amount: 45_000,
        return_percent: -16.4,
        sector_name: "半导体",
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
    fund_recommendations: [
      {
        fund_code: "519674",
        fund_name: "银河创新成长",
        action: "观察",
        points: ["半导体方向资金持续流出，先观察。"],
        risks: ["方向失效时应停止新增。"],
      },
    ],
    summary: "组合已触及浮亏复核线，本次以控制风险为主。",
    recommendations: [],
    caveats: ["数据来自公开行情，存在延迟。", "本报告不构成投资建议。"],
    provider: "deepseek-chat",
    ...overrides,
  };
}

/**
 * 与后端落库形状保持一致：服务端已在 `prepare_analysis_bundle` 里把完整载荷收敛为
 * `compact_fund_lookthrough_for_llm` 的有界摘要才落库，所以暴露列表带 `top_` 前缀。
 * 这份 fixture 的键名必须来自那份契约——曾经按完整载荷的无前缀键名写前端、又按
 * 前缀键名写 fixture，结果测试全绿而生产渲染成空壳。
 */
function lookthroughFacts(overrides: Record<string, unknown> = {}) {
  return {
    fund_lookthrough: {
      schema_version: "fund_lookthrough_research.v1",
      status: "qualified",
      scope: "portfolio_only",
      research_qualified: true,
      execution_qualified: false,
      reason_codes: [],
      raw_holdings_included: false,
      portfolio: {
        scope: "fund_holdings_only",
        portfolio_positions_complete: false,
        disclosed_security_mass_lower_bound_percent: 61,
        identity_known_security_mass_lower_bound_percent: 58,
        unknown_account_mass_percent: 39,
        top_security_exposure_lower_bounds: [
          {
            security_key: "600519",
            security_name: "贵州茅台",
            exposure_lower_bound_percent: 7.4,
          },
        ],
        top_industry_exposure_lower_bounds: [
          { industry: "食品饮料", exposure_lower_bound_percent: 18.6 },
        ],
        top_listing_market_exposure_lower_bounds: [],
      },
      candidates: [],
      ...overrides,
    },
  };
}

describe("ReportPanel 结构化证据", () => {
  it("把风险告警按严重度渲染出来，而不是只留一个风险徽标", () => {
    render(<ReportPanel report={sampleReport()} />);

    const alerts = screen.getByTestId("report-risk-alerts");
    // high 直出，并且带上原本从不展示的 code 与 evidence。
    expect(within(alerts).getByText("组合浮亏触线")).toBeInTheDocument();
    expect(
      within(alerts).getByText(/组合相对持仓成本浮亏 16.40%/),
    ).toBeInTheDocument();
    expect(
      within(alerts).getByText(/依据：按当前持仓金额加权的估算持有收益率/),
    ).toBeInTheDocument();

    // medium 默认收起，避免逐只浮亏告警淹没摘要区。
    expect(within(alerts).queryByText("集中度超限")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /另有 1 条中等风险提醒/ }));
    expect(within(alerts).getByText("集中度超限")).toBeInTheDocument();
  });

  // 原本这里有一条「决策轨道的支撑证据一步反映真实告警条数」的用例，断言那条 6 格
  // 「日报决策轨道」里 05 格的「风险提醒 · N 条」。该轨道已在同期的常驻内容精简中整条
  // 删除（逐格与本屏已有内容重复，见 ReportSummaryHero 内的说明），所以这条用例测的
  // UI 不存在了，随之移除。告警条数本身仍由上一条用例经 `report-risk-alerts` 覆盖。

  it("成品视图渲染使用边界，而不是只在流式骨架里闪一下", () => {
    render(<ReportPanel report={sampleReport()} />);

    const caveats = screen.getByTestId("report-caveats");
    expect(within(caveats).getByText(/使用边界与免责声明（2 条）/)).toBeInTheDocument();
    expect(within(caveats).getByText("数据来自公开行情，存在延迟。")).toBeInTheDocument();
  });

  it("把运行诊断与免责声明分开，不把模型名当成风险披露", () => {
    render(
      <ReportPanel
        report={sampleReport({
          caveats: [
            "数据来自公开行情，存在延迟。",
            "分析管线：deep 模式 / 模型 deepseek-chat，深度审校已应用；当日要闻 3 条。",
            "板块信号回测：半导体 涨后回吐命中率 48%。",
          ],
        })}
      />,
    );

    const caveats = screen.getByTestId("report-caveats");
    // 标题只统计真正的使用边界，不把管线遥测算进免责条数。
    expect(within(caveats).getByText(/使用边界与免责声明（1 条）/)).toBeInTheDocument();
    const diagnostics = screen.getByTestId("report-caveats-diagnostics");
    expect(within(diagnostics).getByText(/分析管线/)).toBeInTheDocument();
    expect(within(diagnostics).getByText(/板块信号回测/)).toBeInTheDocument();
  });

  it("没有 caveats 的历史报告不渲染空的使用边界区块", () => {
    render(<ReportPanel report={sampleReport({ caveats: [] })} />);
    expect(screen.queryByTestId("report-caveats")).not.toBeInTheDocument();
  });
});

describe("ReportPanel 风险升级判定", () => {
  function reportWithEscalation(escalationMode: string, action: string): Report {
    const base = sampleReport();
    return {
      ...base,
      fund_recommendations: [{ ...base.fund_recommendations[0], action }],
      analysis_facts: {
        // 是否生效由本次运行的守卫模式决定，逐报告冻结在 pipeline 里。
        pipeline: { decision_escalation_mode: escalationMode },
        holdings: [
          {
            fund_code: "519674",
            escalation: {
              min_bucket: 0,
              min_action_label: "减仓评估",
              reasons: [
                "量价背离信号显著，且当前持仓板块方向不构成机会",
                "该基金自身量化证据不足以支撑继续观望",
              ],
              suggested_position_change_percent: -25,
              basis: "量价背离信号显著",
            },
          },
        ],
      },
    };
  }

  function openProfessionalEvidence() {
    // 「观察」类建议落在「继续观察」分组、卡片默认收起，此时「专业依据」折叠块
    // 还没挂载，必须先展开卡片本身。
    const expandCard = screen.queryByRole("button", { name: /^展开 银河创新成长$/ });
    if (expandCard) {
      fireEvent.click(expandCard);
    }
    fireEvent.click(screen.getByRole("button", { name: /专业依据/ }));
  }

  it("灰度期展示升级依据，同时说明最终动作未被收紧", () => {
    render(<ReportPanel report={reportWithEscalation("shadow", "观察")} />);
    openProfessionalEvidence();

    const block = screen.getByTestId("report-escalation-evidence");
    expect(within(block).getByText(/对应更保守动作：减仓评估/)).toBeInTheDocument();
    expect(
      within(block).getByText(/量价背离信号显著，且当前持仓板块方向不构成机会/),
    ).toBeInTheDocument();
    // 卡头显示「观察」，这里必须说明升级未生效，否则两处结论互相矛盾。
    expect(within(block).getByText(/未按该判定收紧/)).toBeInTheDocument();
  });

  it("enforced 模式即使动作文案不同也不误报为观察期", () => {
    // 「风控复核」是 REDUCE 档的措辞变体，与 min_action_label「减仓评估」保守度一致。
    // 按文案相等判断会误写"处于观察期"，所以必须以守卫模式为准。
    render(<ReportPanel report={reportWithEscalation("enforced", "风控复核")} />);
    openProfessionalEvidence();

    const block = screen.getByTestId("report-escalation-evidence");
    expect(within(block).getByText(/已参与本次最终动作的收紧/)).toBeInTheDocument();
    expect(within(block).queryByText(/观察期/)).not.toBeInTheDocument();
  });

  it("未触发升级的持仓不渲染该区块", () => {
    const base = sampleReport();
    render(
      <ReportPanel
        report={{
          ...base,
          analysis_facts: {
            holdings: [
              {
                fund_code: "519674",
                escalation: {
                  min_bucket: null,
                  min_action_label: "",
                  reasons: [],
                  suggested_position_change_percent: null,
                  basis: "",
                },
              },
            ],
          },
        }}
      />,
    );
    openProfessionalEvidence();
    expect(screen.queryByTestId("report-escalation-evidence")).not.toBeInTheDocument();
  });
});

describe("ReportPanel 组合穿透", () => {
  function openLookthrough() {
    fireEvent.click(screen.getByRole("button", { name: "组合穿透重复暴露" }));
  }

  it("展示跨基金重复暴露，并把下界与未知质量都写清楚", () => {
    render(
      <ReportPanel report={sampleReport({ analysis_facts: lookthroughFacts() })} />,
    );
    openLookthrough();

    const panel = screen.getByTestId("report-lookthrough");
    // 数字必须标成下界，不能让用户读成精确持仓占比。
    expect(within(panel).getByText("≥ 7.4%")).toBeInTheDocument();
    expect(within(panel).getByText("贵州茅台（600519）")).toBeInTheDocument();
    expect(within(panel).getByText("≥ 18.6%")).toBeInTheDocument();
    // 未披露部分必须显式保留为未知。
    expect(within(panel).getByText(/其余约 39% 未披露/)).toBeInTheDocument();
    expect(within(panel).getByText(/未列出不等于没有/)).toBeInTheDocument();
    expect(within(panel).getByText(/不能作为买入理由/)).toBeInTheDocument();
    // 没有上市地数据时不渲染空分区。
    expect(
      within(panel).queryByTestId("report-lookthrough-markets"),
    ).not.toBeInTheDocument();
  });

  it("拿不到披露数据时明确说明，而不是显示空面板", () => {
    render(
      <ReportPanel
        report={sampleReport({
          analysis_facts: lookthroughFacts({
            status: "unavailable",
            portfolio: {},
          }),
        })}
      />,
    );
    openLookthrough();

    expect(screen.getByTestId("report-lookthrough-unavailable")).toHaveTextContent(
      /未取得可用的基金定期报告披露数据/,
    );
    expect(screen.queryByTestId("report-lookthrough")).not.toBeInTheDocument();
  });

  it("历史报告没有穿透字段时不出现该入口", () => {
    render(<ReportPanel report={sampleReport({ analysis_facts: {} })} />);
    expect(
      screen.queryByRole("button", { name: "组合穿透重复暴露" }),
    ).not.toBeInTheDocument();
  });
});
