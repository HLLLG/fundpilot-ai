// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildPortfolioHeroDisplay,
  hasPortfolioDashboardContent,
  isPortfolioDataForRange,
  PortfolioDashboard,
  portfolioDashboardDataDate,
} from "@/components/PortfolioDashboard";
import { fetchPortfolioDashboard, type PortfolioDashboardData } from "@/lib/api";

const TEST_USER_ID = 101;

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, fetchPortfolioDashboard: vi.fn() };
});

vi.mock("@/components/DailyProfitTop5", () => ({
  DailyProfitTop5: () => <div data-testid="daily-profit-contributors" />,
}));
vi.mock("@/components/HoldingDonutChart", () => ({
  HoldingDonutChart: () => <div data-testid="holding-allocation" />,
}));
vi.mock("@/components/ProfitLossCalendar", () => ({
  ProfitLossCalendar: () => <div data-testid="profit-calendar" />,
}));
vi.mock("@/components/PortfolioRiskMetricsPanel", () => ({
  PortfolioRiskMetricsPanel: () => <div data-testid="portfolio-risk" />,
}));
vi.mock("@/components/PortfolioFactorScoresPanel", () => ({
  PortfolioFactorScoresPanel: ({ enabled }: { enabled: boolean }) =>
    enabled ? <div data-testid="factor-scores" /> : null,
}));
vi.mock("@/components/PortfolioEvidenceOverviewPanel", () => ({
  PortfolioEvidenceOverviewPanel: ({ enabled }: { enabled: boolean }) =>
    enabled ? <div data-testid="evidence-overview" /> : null,
}));
vi.mock("@/components/FactorIcStatusBadge", () => ({ FactorIcStatusBadge: () => null }));
vi.mock("@/components/EvidenceMaturityPanel", () => ({
  EvidenceMaturityPanel: ({ enabled }: { enabled: boolean }) =>
    enabled ? <div data-testid="evidence-maturity-panel" /> : null,
}));
vi.mock("@/components/ProfitAnalysisTrendChart", () => ({
  ProfitAnalysisTrendChart: ({ trend }: { trend?: { kind?: string } | null }) => (
    <div data-testid="profit-trend-kind">{trend?.kind ?? "none"}</div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.sessionStorage.clear();
});

function todayDashboard(): PortfolioDashboardData {
  return {
    summary: {
      daily_profit: 88,
      daily_return_percent: 0.8,
      updated_at: "2026-07-11T10:30:00+08:00",
    },
    history: [],
    allocation: [],
    snapshot_count: 1,
    latest_snapshot_date: "2026-07-10",
    profit_range: "today",
    profit_trend: {
      kind: "intraday",
      trade_date: "2026-07-11",
      points: [
        { time: "09:30", portfolio_percent: 0.1, index_percent: 0.05 },
        { time: "10:30", portfolio_percent: 0.8, index_percent: 0.3 },
      ],
    },
    profit_trend_footer: {
      portfolio_return_percent: 0.8,
      index_return_percent: 0.3,
      alpha_percent: 0.5,
    },
  };
}

function weekDashboard(): PortfolioDashboardData {
  return {
    summary: {
      daily_profit: 9999,
      daily_return_percent: 9.99,
      updated_at: "2026-07-10T18:00:00+08:00",
    },
    history: [],
    allocation: [],
    snapshot_count: 5,
    latest_snapshot_date: "2026-07-10",
    profit_range: "week",
    profit_trend: {
      kind: "daily",
      points: [
        { date: "2026-07-06", portfolio_percent: 0.3, index_percent: 0.1 },
        { date: "2026-07-10", portfolio_percent: 2.34, index_percent: 1.1 },
      ],
    },
    profit_trend_footer: {
      portfolio_return_percent: 2.34,
      index_return_percent: 1.1,
      alpha_percent: 1.24,
    },
  };
}

function emptyDashboard(): PortfolioDashboardData {
  return {
    summary: {},
    history: [],
    allocation: [],
    snapshot_count: 0,
    latest_snapshot_date: null,
    profit_range: "today",
    profit_trend: {
      kind: "intraday",
      trade_date: "2026-07-11",
      points: [],
    },
    profit_trend_footer: {
      portfolio_return_percent: null,
      index_return_percent: null,
      alpha_percent: null,
    },
    daily_top5: { gainers: [], losers: [] },
  };
}

function expectBefore(first: HTMLElement, second: HTMLElement) {
  expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
}

describe("PortfolioDashboard hero derivation", () => {
  it("uses the interval footer return and never the daily amount outside today", () => {
    const display = buildPortfolioHeroDisplay({
      profitRange: "week",
      showTodayReturn: false,
      summary: weekDashboard().summary,
      footer: weekDashboard().profit_trend_footer,
    });

    expect(display).toMatchObject({
      label: "本周累计收益率",
      value: 2.34,
      valueFormat: "percent",
      secondaryPercent: null,
      showMetricToggle: false,
    });
    expect(display.value).not.toBe(9999);
  });

  it("keeps the amount/return switch for today only", () => {
    const amount = buildPortfolioHeroDisplay({
      profitRange: "today",
      showTodayReturn: false,
      summary: todayDashboard().summary,
      footer: todayDashboard().profit_trend_footer,
    });
    const percent = buildPortfolioHeroDisplay({
      profitRange: "today",
      showTodayReturn: true,
      summary: todayDashboard().summary,
      footer: todayDashboard().profit_trend_footer,
    });

    expect(amount).toMatchObject({ label: "当日收益", value: 88, valueFormat: "money" });
    expect(percent).toMatchObject({ label: "当日收益率", value: 0.8, valueFormat: "percent" });
  });

  it("rejects stale range data and derives the visible range end date", () => {
    expect(isPortfolioDataForRange(todayDashboard(), "week")).toBe(false);
    expect(isPortfolioDataForRange(weekDashboard(), "week")).toBe(true);
    expect(portfolioDashboardDataDate(todayDashboard())).toBe("2026-07-11");
    expect(portfolioDashboardDataDate(weekDashboard())).toBe("2026-07-10");
  });

  it("distinguishes a successful but empty dashboard from meaningful data", () => {
    expect(hasPortfolioDashboardContent(emptyDashboard())).toBe(false);
    expect(hasPortfolioDashboardContent(todayDashboard())).toBe(true);
  });
});

describe("PortfolioDashboard range UI", () => {
  it("shows the selected interval return, explanation, and data date", async () => {
    vi.mocked(fetchPortfolioDashboard).mockImplementation(async (params) =>
      params?.range === "week" ? weekDashboard() : todayDashboard(),
    );

    render(<PortfolioDashboard userId={TEST_USER_ID} />);

    await waitFor(() => expect(screen.getByText("+88.00")).toBeInTheDocument());
    expect(screen.getByText(/数据截至 2026-07-11/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "本周" }));

    await waitFor(() => expect(screen.getByText("本周累计收益率")).toBeInTheDocument());
    expect(screen.getAllByText("+2.34%").length).toBeGreaterThan(0);
    expect(screen.queryByText("+9,999.00")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "收益额" })).not.toBeInTheDocument();
    expect(screen.getByText(/所选区间内每日收益率按复利累计/)).toBeInTheDocument();
    expect(screen.getByText(/数据截至 2026-07-10/)).toBeInTheDocument();
    expect(screen.getByTestId("profit-trend-kind")).toHaveTextContent("daily");
  });

  it("orders daily insights before risk and keeps professional evidence collapsed", async () => {
    vi.mocked(fetchPortfolioDashboard).mockResolvedValue(todayDashboard());

    render(<PortfolioDashboard userId={TEST_USER_ID} />);

    await screen.findByTestId("portfolio-analysis-content");
    const trend = screen.getByTestId("profit-trend-kind");
    const contributors = screen.getByTestId("daily-profit-contributors");
    const calendar = screen.getByTestId("profit-calendar");
    const allocation = screen.getByTestId("holding-allocation");
    const risk = screen.getByTestId("portfolio-risk");
    const professional = screen.getByTestId("professional-quant-evidence");

    expectBefore(trend, contributors);
    expectBefore(contributors, calendar);
    expectBefore(calendar, allocation);
    expectBefore(allocation, risk);
    expectBefore(risk, professional);
    expect(screen.queryByTestId("factor-scores")).not.toBeInTheDocument();
    expect(screen.queryByTestId("evidence-overview")).not.toBeInTheDocument();

    const factorButton = screen.getByRole("button", { name: "展开因子评分" });
    fireEvent.click(factorButton);
    expect(screen.getByTestId("factor-scores")).toBeInTheDocument();
    expect(factorButton).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(factorButton.getAttribute("aria-controls") ?? "")).toBeInTheDocument();

    const evidenceButton = screen.getByRole("button", { name: "展开证据总览" });
    fireEvent.click(evidenceButton);
    expect(screen.getByTestId("evidence-overview")).toBeInTheDocument();
    expect(evidenceButton).toHaveAttribute("aria-expanded", "true");
  });

  it("shows an explicit first-load state and does not render empty panels", async () => {
    let resolveRequest: ((data: PortfolioDashboardData) => void) | undefined;
    vi.mocked(fetchPortfolioDashboard).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );

    render(<PortfolioDashboard userId={TEST_USER_ID} />);

    expect(await screen.findByText("正在加载所选区间的盈亏分析…")).toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-analysis-content")).not.toBeInTheDocument();

    await act(async () => resolveRequest?.(todayDashboard()));
    await screen.findByTestId("portfolio-analysis-content");
  });

  it("shows a first-load failure with retry instead of an empty state", async () => {
    vi.mocked(fetchPortfolioDashboard)
      .mockRejectedValueOnce(new Error("网络暂不可用"))
      .mockResolvedValueOnce(todayDashboard());

    render(<PortfolioDashboard userId={TEST_USER_ID} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("盈亏分析加载失败：网络暂不可用");
    expect(screen.queryByText(/暂无可分析/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-analysis-content")).not.toBeInTheDocument();

    fireEvent.click(within(alert).getByRole("button", { name: "重试" }));
    await screen.findByTestId("portfolio-analysis-content");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps cached data visible while updating and after a refresh failure", async () => {
    const now = new Date();
    const cacheKey = `portfolio-dashboard:${TEST_USER_ID}:today:${now.getFullYear()}:${now.getMonth() + 1}`;
    window.sessionStorage.setItem(
      cacheKey,
      JSON.stringify({ fetchedAt: 0, data: todayDashboard() }),
    );
    let rejectRequest: ((reason?: unknown) => void) | undefined;
    vi.mocked(fetchPortfolioDashboard).mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectRequest = reject;
        }),
    );

    render(<PortfolioDashboard userId={TEST_USER_ID} />);

    expect(screen.getByText("+88.00")).toBeInTheDocument();
    expect(await screen.findByText(/正在更新盈亏分析/)).toHaveTextContent("截至 2026-07-11");
    expect(screen.getByTestId("portfolio-analysis-content")).toBeInTheDocument();

    await act(async () => rejectRequest?.(new Error("更新超时")));
    expect(await screen.findByText(/最新盈亏数据更新失败/)).toHaveTextContent("更新超时");
    expect(screen.getByText("+88.00")).toBeInTheDocument();
    expect(screen.getByTestId("portfolio-analysis-content")).toBeInTheDocument();
  });

  it("renders a successful empty response separately from errors", async () => {
    vi.mocked(fetchPortfolioDashboard).mockResolvedValue(emptyDashboard());

    render(<PortfolioDashboard userId={TEST_USER_ID} />);

    expect(await screen.findByText(/暂无可分析的持仓收益数据/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-analysis-content")).not.toBeInTheDocument();
    expect(screen.getByTestId("evidence-maturity-console")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看成熟度" }));
    expect(screen.getByTestId("evidence-maturity-panel")).toBeInTheDocument();
  });

  it("does not reuse a previous account's session cache", async () => {
    vi.mocked(fetchPortfolioDashboard)
      .mockResolvedValueOnce(todayDashboard())
      .mockImplementationOnce(() => new Promise(() => undefined));

    const firstAccount = render(<PortfolioDashboard userId={101} />);
    await screen.findByTestId("portfolio-analysis-content");
    firstAccount.unmount();

    render(<PortfolioDashboard userId={202} />);

    expect(screen.queryByTestId("portfolio-analysis-content")).not.toBeInTheDocument();
    expect(fetchPortfolioDashboard).toHaveBeenCalledTimes(2);
  });
});
