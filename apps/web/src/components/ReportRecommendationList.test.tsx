// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import type { Report } from "@/lib/api";
import { ReportRecommendationList } from "@/components/ReportRecommendationList";

afterEach(cleanup);

type FundRec = Report["fund_recommendations"][number];

function buildReport(
  recommendations: FundRec[],
  snapshots: Report["snapshots"] = [],
  analysisFacts?: Report["analysis_facts"],
): Report {
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
    snapshots,
    market_context: [],
    market_news: [],
    topic_briefs: [],
    fund_recommendations: recommendations,
    recommendations: [],
    caveats: [],
    provider: "test",
    analysis_facts: analysisFacts,
  };
}

function recommendation(overrides: Partial<FundRec>): FundRec {
  return {
    fund_code: "000001",
    fund_name: "测试基金",
    action: "观察",
    points: ["保持观察"],
    ...overrides,
  };
}

function reportWithReduceAndWatch(): Report {
  return buildReport([
    recommendation({
      fund_code: "000001",
      fund_name: "测试减仓基金",
      action: "减仓评估",
      amount_note: "建议降至约 10,500 元",
      points: ["集中度超过上限", "下交易日：若再跌2%则减仓"],
      risks: ["集中度风险"],
    }),
    recommendation({
      fund_code: "000002",
      fund_name: "测试观察基金",
      action: "观察",
      points: ["冲高回落，不追涨"],
    }),
  ]);
}

function reportWithPlaceholderNews(): Report {
  return buildReport([
    recommendation({
      action: "减仓评估",
      news_bullish: ["暂无明确利好", "真实政策利好"],
      news_bearish: ["暂无明确利空"],
      points: ["集中度超过上限"],
    }),
  ]);
}

function reportWithInvalidDiagnostics(): Report {
  return buildReport(
    [recommendation({ action: "减仓评估", points: ["集中度超过上限"] })],
    [
      {
        fund_code: "000001",
        fund_name: "测试基金",
        source: "test",
        return_1y_percent: 8220.94,
        max_drawdown_1y_percent: -160.53,
      },
    ],
  );
}

function reportWithExtremeAction(): Report {
  return buildReport([
    recommendation({ action: "清仓评估", points: ["多重强风险共振"] }),
  ]);
}

function reportWithIcState(
  state: "unavailable" | "stale" | "available",
): Report {
  return buildReport(
    [recommendation({ action: "减仓评估", points: ["集中度超过上限"] })],
    [],
    {
      factor_scores: {
        ic_status: {
          state,
          available: state !== "unavailable",
          stale: state === "stale",
          run_date: state === "stale" ? "2026-05-01" : "2026-07-11",
          source: state === "unavailable" ? "unavailable" : "database",
        },
      },
    },
  );
}

it("renders actionable cards before collapsed observation rows", () => {
  render(<ReportRecommendationList report={reportWithReduceAndWatch()} />);
  expect(screen.getByRole("heading", { name: "需要处理" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "继续观察" })).toBeInTheDocument();
  expect(screen.getByText("建议降至约 10,500 元")).toBeInTheDocument();
  expect(screen.queryByText("完整量化证据")).not.toBeInTheDocument();
});

it("explains an incomplete ledger and links directly to baseline confirmation", () => {
  const onConfirm = vi.fn();
  const report = buildReport(
    [recommendation({ points: ["字段级证据未达到可执行条件，本条仅保留观察/风险复核。"] })],
    [],
    {
      data_evidence_guard: {
        execution_blocked: true,
        reasons_by_fund: {
          "000001": ["incomplete_or_unsettled_position_ledger"],
        },
      },
    },
  );

  render(
    <ReportRecommendationList
      report={report}
      onConfirmLedgerBaseline={onConfirm}
    />,
  );

  expect(screen.getByText("为什么现在只有“观察”？")).toBeInTheDocument();
  expect(screen.getByText(/系统还不能确认每只基金的实际份额和成本/)).toBeInTheDocument();
  expect(screen.queryByText(/字段级证据/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "去确认账本基线" }));
  expect(onConfirm).toHaveBeenCalledOnce();
});

it("keeps observation detail collapsed until the row is opened", () => {
  render(<ReportRecommendationList report={reportWithReduceAndWatch()} />);
  const toggle = screen.getByRole("button", { name: /展开 测试观察基金/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  const summary = document.getElementById("000002-1-summary");
  expect(summary).not.toBeNull();
  expect(within(summary!).getByRole("button", { name: "为什么这样建议" })).toBeInTheDocument();
});

it("filters placeholder news and reveals meaningful news in the why layer", () => {
  render(<ReportRecommendationList report={reportWithPlaceholderNews()} />);
  fireEvent.click(screen.getByRole("button", { name: "为什么这样建议" }));
  expect(screen.queryByText("暂无明确利空")).not.toBeInTheDocument();
  expect(screen.getByText("真实政策利好")).toBeInTheDocument();
});

it("hides impossible diagnostics and explains the omission in professional evidence", () => {
  render(<ReportRecommendationList report={reportWithInvalidDiagnostics()} />);
  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));
  expect(screen.queryByText("8220.94%")).not.toBeInTheDocument();
  expect(screen.getByText("指标数据异常，已隐藏")).toBeInTheDocument();
});

it("reveals the unavailable IC explanation only inside open professional evidence", () => {
  render(<ReportRecommendationList report={reportWithIcState("unavailable")} />);

  expect(screen.queryByText("量化回测未接入")).not.toBeInTheDocument();
  expect(
    screen.queryByText("当前建议主要依据持仓风险、行情与新闻；IC 不参与本次结论。"),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));

  expect(screen.getByText("量化回测未接入")).toBeInTheDocument();
  expect(
    screen.getByText("当前建议主要依据持仓风险、行情与新闻；IC 不参与本次结论。"),
  ).toBeInTheDocument();
});

it("marks stale IC as excluded and shows its run date in professional evidence", () => {
  render(<ReportRecommendationList report={reportWithIcState("stale")} />);

  expect(screen.queryByText(/IC 回测已过期/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));

  expect(
    screen.getByText("IC 回测已过期（2026-05-01），本次已降级为不参与"),
  ).toBeInTheDocument();
});

it("does not add an IC warning when report evidence is available", () => {
  render(<ReportRecommendationList report={reportWithIcState("available")} />);
  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));

  expect(screen.queryByText("量化回测未接入")).not.toBeInTheDocument();
  expect(screen.queryByText(/IC 回测已过期/)).not.toBeInTheDocument();
});

it("shows verified holding tradeability and the lot-level manual-review boundary", () => {
  render(
    <ReportRecommendationList
      report={buildReport([
        recommendation({
          action: "减仓评估",
          tradeability: {
            data_status: "complete",
            freshness: "fresh",
            purchase_state: "open",
            purchase_status: "开放申购",
            redemption_state: "open",
            redemption_status: "开放赎回",
            minimum_initial_purchase_yuan: 10,
            minimum_additional_purchase_yuan: 100,
            daily_purchase_limit_yuan: 5000,
            daily_purchase_limit_unlimited: false,
            source_ids: ["eastmoney.fund_purchase_em"],
            checked_at: "2026-07-14T10:00:00+08:00",
          },
          transaction_execution: {
            add_status: "eligible",
            redemption_status: "eligible",
            acquisition_lot_status: "unverified",
            reduction_amount_status: "manual_review",
          },
        }),
      ])}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));

  expect(screen.getByLabelText("基金交易条件")).toBeInTheDocument();
  expect(screen.getByText("申购开放")).toBeInTheDocument();
  expect(screen.getByText("赎回开放")).toBeInTheDocument();
  expect(screen.getByText("追加门禁通过")).toBeInTheDocument();
  expect(screen.getByText(/逐笔持有期未核验/)).toBeInTheDocument();
});

it("shows the review target and a direct transaction shortcut for a current holding", () => {
  const onApplyTransaction = vi.fn();
  const report = buildReport(
    [recommendation({
      action: "减仓评估",
      transaction_execution: {
        redemption_status: "eligible",
        acquisition_lot_status: "unverified",
        reduction_amount_status: "manual_review",
        review_target_amount_yuan: 1_500,
        review_target_percent: 15,
      },
      tradeability: {
        redemption_state: "open",
        redemption_fee_tiers: [{ condition: "持有不少于 7 天", fee_percent: 0.5 }],
      },
    })],
    [{
      fund_code: "000001",
      fund_name: "测试基金",
      latest_nav: 1.5,
      nav_date: "2026-07-17",
      source: "test",
    }],
  );

  render(
    <ReportRecommendationList
      report={report}
      currentHoldings={[{
        fund_code: "000001",
        fund_name: "测试基金",
        holding_amount: 10_000,
        return_percent: 0,
      }]}
      onApplyTransaction={onApplyTransaction}
    />,
  );

  expect(screen.getByText("¥1,500")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /核对并记录/ })).toBeInTheDocument();
  expect(screen.getByText(/回填实际卖出份额即可更新持仓/)).toBeInTheDocument();
});

it("keeps extreme actions behind the existing confirmation gate", () => {
  render(<ReportRecommendationList report={reportWithExtremeAction()} />);
  expect(screen.getByTestId("extreme-action-gate")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "为什么这样建议" })).not.toBeInTheDocument();
});

it("preserves card state within one report and resets it when the report id changes", () => {
  const firstReport = reportWithExtremeAction();
  const sameReport = { ...firstReport, title: "同一份日报的新引用" };
  const nextReport = { ...firstReport, id: "report-2", title: "下一份日报" };
  const { rerender } = render(<ReportRecommendationList report={firstReport} />);

  fireEvent.click(screen.getByTestId("extreme-action-gate"));
  fireEvent.click(screen.getByRole("button", { name: "为什么这样建议" }));
  expect(screen.getByRole("button", { name: "为什么这样建议" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );

  rerender(<ReportRecommendationList report={sameReport} />);
  expect(screen.queryByTestId("extreme-action-gate")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "为什么这样建议" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );

  rerender(<ReportRecommendationList report={nextReport} />);
  expect(screen.getByTestId("extreme-action-gate")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "为什么这样建议" })).not.toBeInTheDocument();
});

it("keeps the position percentage but omits a translated basis already used in the summary", () => {
  render(
    <ReportRecommendationList
      report={buildReport([
        recommendation({
          action: "减仓评估",
          points: ["其他依据"],
          suggested_position_change_percent: -20,
          suggested_position_change_basis: "  return_1y_percent 12.5%  ",
        }),
      ])}
    />,
  );

  expect(screen.getByText("建议减仓 20%")).toBeInTheDocument();
  expect(screen.getAllByText("近1年收益 12.5%")).toHaveLength(1);
});

it("omits a translated fallback point already used as the primary reason", () => {
  render(
    <ReportRecommendationList
      report={buildReport([
        recommendation({
          action: "减仓评估",
          points: ["  momentum分位19  "],
        }),
      ])}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "为什么这样建议" }));
  expect(screen.getAllByText("动量分位19")).toHaveLength(1);
});

it("renders a sole next-trading-day point only once", () => {
  render(
    <ReportRecommendationList
      report={buildReport([
        recommendation({
          action: "减仓评估",
          points: ["  下交易日：若再跌2%则减仓  "],
        }),
      ])}
    />,
  );

  expect(screen.getAllByText("下交易日：若再跌2%则减仓")).toHaveLength(1);
});

it("deduplicates translated news exact matches without removing longer reasons", () => {
  render(
    <ReportRecommendationList
      report={buildReport([
        recommendation({
          action: "减仓评估",
          amount_note: "独立摘要原因",
          points: [
            "momentum分位19",
            "动量分位19但估值偏高",
            "return_1y_percent 8%",
          ],
          news_bullish: ["动量分位19"],
          news_bearish: ["近1年收益 8%"],
        }),
      ])}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "为什么这样建议" }));
  const why = document.getElementById("000001-0-why");
  expect(why).not.toBeNull();
  expect(within(why!).getByText("有效利好")).toBeInTheDocument();
  expect(within(why!).getByText("有效利空 / 风险")).toBeInTheDocument();
  expect(within(why!).getAllByText("动量分位19")).toHaveLength(1);
  expect(within(why!).getAllByText("近1年收益 8%")).toHaveLength(1);
  expect(within(why!).getByText("动量分位19但估值偏高")).toBeInTheDocument();
});

it("restores the formatted amount fallback when no stronger amount detail exists", () => {
  render(
    <ReportRecommendationList
      report={buildReport([
        recommendation({
          action: "减仓评估",
          amount_yuan: 10500,
          points: ["集中度超过上限"],
        }),
      ])}
    />,
  );

  expect(screen.getByText("参考金额：约 10,500 元")).toBeInTheDocument();
});

it("does not repeat the formatted amount fallback already used as the primary reason", () => {
  render(
    <ReportRecommendationList
      report={buildReport([
        recommendation({
          action: "减仓评估",
          amount_yuan: 10500,
          points: ["  参考金额：约 10,500 元  "],
        }),
      ])}
    />,
  );

  expect(screen.getAllByText("参考金额：约 10,500 元")).toHaveLength(1);
});

it("binds duplicate fund codes to snapshots and facts by authoritative holding index", () => {
  const recommendations = [
    recommendation({
      fund_code: "000000",
      fund_name: "未知基金甲",
      action: "减仓评估",
      points: ["甲基金风险"],
    }),
    recommendation({
      fund_code: "000000",
      fund_name: "未知基金乙",
      action: "减仓评估",
      points: ["乙基金风险"],
    }),
  ];
  const report = buildReport(
    recommendations,
    [
      {
        fund_code: "000000",
        fund_name: "未知基金甲",
        latest_nav: 1.01,
        nav_date: "2026-07-10",
        source: "test-a",
      },
      {
        fund_code: "000000",
        fund_name: "未知基金乙",
        latest_nav: 2.02,
        nav_date: "2026-07-11",
        source: "test-b",
      },
    ],
    {
      holdings: [
        {
          fund_code: "000000",
          sector_opportunity: {
            sector_label: "板块甲",
            score: 61,
          },
        },
        {
          fund_code: "000000",
          sector_opportunity: {
            sector_label: "板块乙",
            score: 82,
          },
        },
      ],
    },
  );
  report.holdings = [
    {
      fund_code: "000000",
      fund_name: "未知基金甲",
      holding_amount: 1_000,
      return_percent: 0,
    },
    {
      fund_code: "000000",
      fund_name: "未知基金乙",
      holding_amount: 2_000,
      return_percent: 0,
    },
  ];

  render(<ReportRecommendationList report={report} />);

  const firstToggle = screen.getByRole("button", { name: "收起 未知基金甲" });
  const secondToggle = screen.getByRole("button", { name: "收起 未知基金乙" });
  expect(firstToggle).toHaveAttribute("aria-controls", "000000-0-summary");
  expect(secondToggle).toHaveAttribute("aria-controls", "000000-1-summary");

  const firstSummary = document.getElementById("000000-0-summary");
  const secondSummary = document.getElementById("000000-1-summary");
  expect(firstSummary).not.toBeNull();
  expect(secondSummary).not.toBeNull();

  fireEvent.click(within(secondSummary!).getByRole("button", { name: "专业依据" }));
  expect(within(secondSummary!).getByText("最新净值 2.02 · 日期 2026-07-11")).toBeInTheDocument();
  expect(within(secondSummary!).getByText("板块乙")).toBeInTheDocument();
  expect(within(secondSummary!).queryByText("最新净值 1.01 · 日期 2026-07-10")).not.toBeInTheDocument();
  expect(within(secondSummary!).queryByText("板块甲")).not.toBeInTheDocument();
});
