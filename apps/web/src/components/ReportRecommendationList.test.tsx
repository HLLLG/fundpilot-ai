// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it } from "vitest";
import type { Report } from "@/lib/api";
import { ReportRecommendationList } from "@/components/ReportRecommendationList";

afterEach(cleanup);

type FundRec = Report["fund_recommendations"][number];

function buildReport(
  recommendations: FundRec[],
  snapshots: Report["snapshots"] = [],
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

it("renders actionable cards before collapsed observation rows", () => {
  render(<ReportRecommendationList report={reportWithReduceAndWatch()} />);
  expect(screen.getByRole("heading", { name: "需要处理" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "继续观察" })).toBeInTheDocument();
  expect(screen.getByText("建议降至约 10,500 元")).toBeInTheDocument();
  expect(screen.queryByText("完整量化证据")).not.toBeInTheDocument();
});

it("keeps observation detail collapsed until the row is opened", () => {
  render(<ReportRecommendationList report={reportWithReduceAndWatch()} />);
  const toggle = screen.getByRole("button", { name: /展开 测试观察基金/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  const summary = document.getElementById("000002-summary");
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

it("keeps extreme actions behind the existing confirmation gate", () => {
  render(<ReportRecommendationList report={reportWithExtremeAction()} />);
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
  const why = document.getElementById("000001-why");
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
