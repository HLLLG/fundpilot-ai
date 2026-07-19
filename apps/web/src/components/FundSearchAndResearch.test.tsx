// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FundResearchDetail } from "@/components/FundResearchDetail";
import { FundSearchDialog } from "@/components/FundSearchDialog";
import {
  fetchFundPublicOverview,
  fetchSectorIntraday,
  searchFundsPage,
  type FundPublicOverview,
  type Holding,
} from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchFundPublicOverview: vi.fn(),
    fetchSectorIntraday: vi.fn(),
    searchFundsPage: vi.fn(),
  };
});

vi.mock("@/components/PerformanceTrendPanel", () => ({
  PerformanceTrendPanel: (props: {
    benchmarkSymbol?: string | null;
    showTransactions?: boolean;
    initialFundHistory?: FundPublicOverview["nav_history"];
    initialFundHistoryCoverageDays?: number;
    chartHeight?: number;
  }) => (
    <div
      data-testid="performance-panel"
      data-benchmark={props.benchmarkSymbol ?? "none"}
      data-transactions={String(props.showTransactions)}
      data-initial-points={props.initialFundHistory?.points.length ?? 0}
      data-coverage-days={props.initialFundHistoryCoverageDays}
      data-chart-height={props.chartHeight}
    />
  ),
}));

vi.mock("@/components/IntradayPercentChart", () => ({
  IntradayPercentChart: (props: { height?: number }) => (
    <div data-testid="intraday-chart" data-height={props.height} />
  ),
}));

function overview(): FundPublicOverview {
  return {
    fund_code: "008586",
    fund_name: "华夏中证人工智能主题ETF联接C",
    fund_type: "指数型-股票",
    latest_nav: 1.6347,
    nav_date: "2026-07-17",
    official_daily_return_percent: -7.61,
    official_return_status: "available",
    returns: {
      one_month_percent: 3.2,
      three_month_percent: 12.4,
      six_month_percent: 26.8,
      one_year_percent: 84.73,
    },
    management_fee: "0.50%",
    fund_scale_yi: 18.5,
    fund_scale_as_of: "2026-06-30",
    max_drawdown_1y_percent: -18.2,
    relation: {
      status: "available",
      kind: "tracking_reference",
      label: "人工智能",
      source_type: "index",
      source_code: "930713",
      source_name: "中证人工智能",
      confidence: 0.68,
      evidence_tier: "third_party_reference",
      evidence_source: "xq_akshare_aggregator",
      price_proxy_eligible: true,
      note: "精确指数身份可作跟踪标的行情参考；基金净值仍以官方披露为准。",
    },
    performance_benchmark: {
      symbol: "930713",
      name: "中证人工智能主题指数",
      kind: "tracking_reference",
      source: "third_party_reference",
    },
    nav_history: {
      fund_code: "008586",
      fund_name: "华夏中证人工智能主题ETF联接C",
      source: "test",
      points: [
        { date: "2026-07-16T00:00:00", nav: 1.7, daily_return_percent: 1.2 },
        { date: "2026-07-17T00:00:00", nav: 1.6347, daily_return_percent: -7.61 },
      ],
    },
    is_held: true,
    data_note: "基金涨跌与收益率均来自官方净值序列；关联行情仅作参考。",
  };
}

const holding: Holding = {
  fund_code: "008586",
  fund_name: "华夏中证人工智能主题ETF联接C",
  holding_amount: 10000,
  return_percent: 20,
  holding_profit: 2000,
  holding_return_percent: 20,
  daily_profit: -801,
  daily_return_percent: -8.01,
  daily_return_percent_source: "sector_estimate",
  daily_return_is_estimated: true,
};

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(fetchFundPublicOverview).mockResolvedValue(overview());
  vi.mocked(fetchSectorIntraday).mockResolvedValue({
    points: [
      { time: "09:30", percent: -1 },
      { time: "15:00", percent: -8.01 },
    ],
    close_change_percent: -8.01,
    source_type: "index",
    source_name: "中证人工智能",
    session_date: "2026-07-17",
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("global fund search", () => {
  it("searches by keyword and returns the selected fund", async () => {
    vi.mocked(searchFundsPage).mockResolvedValue({
      items: [
        {
          fund_code: "008586",
          fund_name: "华夏中证人工智能主题ETF联接C",
          match_kind: "code_exact",
        },
      ],
      total: 1,
      offset: 0,
      limit: 5,
      has_more: false,
    });
    const onSelect = vi.fn();
    render(<FundSearchDialog open onClose={() => undefined} onSelect={onSelect} />);

    fireEvent.change(screen.getByRole("searchbox", { name: "输入基金名称或代码" }), {
      target: { value: "008586" },
    });

    expect(await screen.findByText("华夏中证人工智能主题ETF联接C")).toBeInTheDocument();
    expect(searchFundsPage).toHaveBeenCalledWith("008586", 5, 0);
    fireEvent.click(screen.getByRole("button", { name: /华夏中证人工智能主题ETF联接C/ }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ fund_code: "008586" }));
  });

  it("shows recent selections and progressively loads every matching fund", async () => {
    const firstFive = Array.from({ length: 5 }, (_, index) => ({
      fund_code: `00000${index + 1}`,
      fund_name: `华夏示例基金${index + 1}`,
      match_kind: "name_prefix" as const,
      popularity_rank: index + 1,
    }));
    vi.mocked(searchFundsPage)
      .mockResolvedValueOnce({
        items: firstFive,
        total: 7,
        offset: 0,
        limit: 5,
        has_more: true,
      })
      .mockResolvedValueOnce({
        items: [
          { fund_code: "000006", fund_name: "华夏示例基金6", match_kind: "name_prefix" },
          { fund_code: "000007", fund_name: "华夏示例基金7", match_kind: "name_prefix" },
        ],
        total: 7,
        offset: 5,
        limit: 50,
        has_more: false,
      });

    const onSelect = vi.fn();
    const rendered = render(<FundSearchDialog open onClose={() => undefined} onSelect={onSelect} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "输入基金名称或代码" }), {
      target: { value: "华夏" },
    });

    expect(await screen.findByText("热门匹配")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "更多匹配（2 只）" }));
    expect(await screen.findByRole("button", { name: /华夏示例基金7/ })).toBeInTheDocument();
    expect(searchFundsPage).toHaveBeenLastCalledWith("华夏", 50, 5);

    fireEvent.click(screen.getByRole("button", { name: /华夏示例基金1/ }));
    rendered.unmount();
    render(<FundSearchDialog open onClose={() => undefined} onSelect={() => undefined} />);
    expect(screen.getByText("最近搜索")).toBeInTheDocument();
    expect(screen.getByText("华夏示例基金1")).toBeInTheDocument();
  });
});

describe("read-only fund research detail", () => {
  it("shows a compact user-facing sector view and estimated holding values", async () => {
    render(
      <FundResearchDetail
        fund={{ fund_code: "008586", fund_name: "华夏人工智能ETF联接C" }}
        holding={holding}
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText("官方日涨幅 · 2026-07-17")).toBeInTheDocument();
    expect(screen.getByText("-7.61%")).toBeInTheDocument();
    expect(screen.queryByText("数据口径已分离")).not.toBeInTheDocument();
    const prefetchedPerformance = screen.getByTestId("performance-panel");
    expect(prefetchedPerformance.closest("section")).toHaveAttribute("hidden");
    expect(prefetchedPerformance).toHaveAttribute("data-initial-points", "2");
    expect(prefetchedPerformance).toHaveAttribute("data-coverage-days", "260");
    expect(prefetchedPerformance).toHaveAttribute("data-chart-height", "170");

    fireEvent.click(screen.getByRole("button", { name: "关联板块" }));
    expect(await screen.findByText("中证人工智能")).toBeInTheDocument();
    expect(screen.getByText("930713")).toBeInTheDocument();
    expect(screen.getByText("日期 2026-07-17")).toBeInTheDocument();
    expect(screen.getByText("-8.01%")).toBeInTheDocument();
    expect(screen.queryByText("匹配置信度")).not.toBeInTheDocument();
    const intradayChart = await screen.findByTestId("intraday-chart");
    expect(intradayChart).toHaveAttribute("data-height", "145");

    fireEvent.click(screen.getByRole("button", { name: "业绩走势" }));
    const performance = screen.getByTestId("performance-panel");
    expect(performance.closest("section")).not.toHaveAttribute("hidden");
    expect(performance).toHaveAttribute("data-benchmark", "930713");
    expect(performance).toHaveAttribute("data-transactions", "true");

    fireEvent.click(screen.getByRole("button", { name: "我的收益" }));
    expect(screen.getByText("估算当日收益")).toBeInTheDocument();
    expect(screen.getByText(/≈板块参考估算/)).toBeInTheDocument();
  });
});
