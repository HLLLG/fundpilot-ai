// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type {
  DiscoveryOutcomesPayload,
  PortfolioEvidenceOverview,
  PortfolioRiskMetrics,
  RebalanceSimulation,
  RecommendationAccuracy,
  ReportOutcomes,
  SectorSignalBacktest,
  ShadowEscalationDigest,
} from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
  fetchDiscoveryOutcomes: vi.fn(),
  fetchPortfolioEvidenceOverview: vi.fn(),
  fetchPortfolioRiskMetrics: vi.fn(),
  fetchRebalanceSimulation: vi.fn(),
  fetchRecommendationAccuracy: vi.fn(),
  fetchReportOutcomes: vi.fn(),
  fetchSectorSignalBacktest: vi.fn(),
  fetchShadowEscalationDigest: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

import { DiscoveryOutcomesPanel } from "@/components/DiscoveryOutcomesPanel";
import { PortfolioEvidenceOverviewPanel } from "@/components/PortfolioEvidenceOverviewPanel";
import { PortfolioRiskMetricsPanel } from "@/components/PortfolioRiskMetricsPanel";
import { RebalanceSimulationPanel } from "@/components/RebalanceSimulationPanel";
import { RecommendationAccuracyPanel } from "@/components/RecommendationAccuracyPanel";
import { ReportOutcomesPanel } from "@/components/ReportOutcomesPanel";
import { SectorSignalBacktestPanel } from "@/components/SectorSignalBacktestPanel";
import { ShadowEscalationDigestCard } from "@/components/ShadowEscalationDigestCard";

const decisionMetrics = {
  gross_direction: {
    eligible_count: 3,
    mature_count: 3,
    unavailable_count: 0,
    hit_count: 2,
    miss_count: 1,
    coverage_percent: 100,
    hit_rate_percent: 66.7,
  },
  positive_net_return: {
    eligible_count: 3,
    mature_count: 2,
    unavailable_count: 1,
    hit_count: 1,
    miss_count: 1,
    coverage_percent: 66.7,
    hit_rate_percent: 50,
  },
  gross_excess: {
    eligible_count: 3,
    mature_count: 1,
    unavailable_count: 2,
    hit_count: 1,
    miss_count: 0,
    coverage_percent: 33.3,
    hit_rate_percent: 100,
  },
  net_excess: {
    eligible_count: 3,
    mature_count: 1,
    unavailable_count: 2,
    hit_count: 0,
    miss_count: 1,
    coverage_percent: 33.3,
    hit_rate_percent: 0,
  },
};

afterEach(() => {
  cleanup();
  Object.values(apiMocks).forEach((mock) => mock.mockReset());
  window.localStorage.clear();
});

describe("truthful evidence panel states", () => {
  it("does not disguise a risk-metrics request failure as insufficient history", async () => {
    const unavailable: PortfolioRiskMetrics = {
      available: false,
      sample_days: 4,
      message: "当前只有 4 个交易日样本。",
    };
    apiMocks.fetchPortfolioRiskMetrics
      .mockRejectedValueOnce(new Error("风险服务超时"))
      .mockResolvedValueOnce(unavailable);

    render(<PortfolioRiskMetricsPanel />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("风险指标加载失败：风险服务超时");
    expect(alert).not.toHaveTextContent("满 20 个交易日");

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("当前只有 4 个交易日样本。")).toBeInTheDocument();
    expect(apiMocks.fetchPortfolioRiskMetrics).toHaveBeenCalledTimes(2);
  });

  it("offers retry for rebalance failures and exposes mobile cards", async () => {
    const simulation: RebalanceSimulation = {
      assumption: "仅模拟报告中的示意金额，不会执行真实交易。",
      current_total: 10_000,
      simulated_total: 10_000,
      concentration_limit_percent: 40,
      warnings: [],
      rows: [
        {
          fund_code: "110022",
          fund_name: "易方达消费行业股票型证券投资基金超长名称",
          action: "减仓",
          current_amount: 6_000,
          delta_yuan: -1_000,
          simulated_amount: 5_000,
          current_weight_percent: 60,
          simulated_weight_percent: 50,
          weight_delta_percent: -10,
          amount_note: "金额仅用于帮助理解报告建议。",
        },
      ],
    };
    apiMocks.fetchRebalanceSimulation
      .mockRejectedValueOnce(new Error("模拟服务暂不可用"))
      .mockResolvedValueOnce(simulation);

    render(<RebalanceSimulationPanel reportId="report-1" embedded />);

    expect(await screen.findByRole("alert")).toHaveTextContent("模拟调仓加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByRole("list", { name: "模拟调仓明细" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("separates a sector-backtest failure from a real no-data response", async () => {
    const emptyBacktest: SectorSignalBacktest = {
      enabled: true,
      has_data: false,
      message: "历史样本不足，暂未形成有效回测。",
    };
    apiMocks.fetchSectorSignalBacktest
      .mockRejectedValueOnce(new Error("回测服务超时"))
      .mockResolvedValueOnce(emptyBacktest);

    render(<SectorSignalBacktestPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent("板块信号回测加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("历史样本不足，暂未形成有效回测。")).toBeInTheDocument();
    expect(apiMocks.fetchSectorSignalBacktest).toHaveBeenCalledTimes(2);
  });

  it("keeps discovery outcome failures actionable and renders the API empty reason after retry", async () => {
    const emptyOutcomes: DiscoveryOutcomesPayload = {
      has_data: false,
      days: 5,
      horizon: "T+5",
      eligible_count: 1,
      mature_count: 0,
      pending_count: 1,
      coverage_percent: 0,
      message: "T+5 尚未成熟，暂不能判定命中。",
      items: [],
    };
    apiMocks.fetchDiscoveryOutcomes
      .mockRejectedValueOnce(new Error("净值服务超时"))
      .mockResolvedValueOnce(emptyOutcomes);

    render(<DiscoveryOutcomesPanel reportId="discovery-1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("荐基 T+5 复盘加载失败：净值服务超时");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("T+5 尚未成熟，暂不能判定命中。")).toBeInTheDocument();
    expect(apiMocks.fetchDiscoveryOutcomes).toHaveBeenLastCalledWith("discovery-1", 5);
  });

  it("shows recommendation-accuracy errors and a distinct successful empty result", async () => {
    const emptyAccuracy: RecommendationAccuracy = {
      has_enough_data: true,
      report_count: 8,
      paired_days: 4,
      by_style: {},
      summary_lines: [],
    };
    apiMocks.fetchRecommendationAccuracy
      .mockRejectedValueOnce(new Error("统计服务不可用"))
      .mockResolvedValueOnce(emptyAccuracy);

    render(<RecommendationAccuracyPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent("T+N 复盘加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(
      await screen.findByText("复盘已生成，但暂无可展示的决策风格样本。"),
    ).toBeInTheDocument();
  });

  it("renders bounded forward T+N maturity and keeps auto tuning disabled", async () => {
    const forwardAccuracy: RecommendationAccuracy = {
      metric_status: "forward_nav_v1",
      is_experimental: true,
      auto_tuning_eligible: false,
      warning: "四口径仅供人工复盘，真实费用与样本量达标前不进入自动调参。",
      has_enough_data: true,
      report_count: 2,
      selected_report_count: 2,
      formal_v2_report_count: 1,
      horizons: [1, 5, 20],
      metrics: decisionMetrics,
      legacy_reference: {
        excluded_from_formal_v2: true,
        report_count: 1,
        recommendation_count: 2,
        eligible_count: 2,
        mature_count: 1,
        hit_rate_percent: 100,
      },
      by_horizon: {
        "T+1": {
          horizon_trading_days: 1,
          eligible_count: 3,
          mature_count: 3,
          skipped_count: 0,
          hit_count: 2,
          miss_count: 1,
          hit_rate_percent: 66.7,
          coverage_percent: 100,
        },
      },
      by_style: {
        tactical: {
          decision_style: "tactical",
          paired_count: 2,
          eligible_count: 3,
          observation_count: 1,
          hit_count: 2,
          miss_count: 1,
          hit_rate_percent: 66.7,
          by_horizon: {
            "T+1": {
              horizon_trading_days: 1,
              eligible_count: 3,
              mature_count: 3,
              skipped_count: 0,
              hit_count: 2,
              miss_count: 1,
              hit_rate_percent: 66.7,
              coverage_percent: 100,
            },
          },
        },
      },
    };
    apiMocks.fetchRecommendationAccuracy.mockResolvedValue(forwardAccuracy);

    render(<RecommendationAccuracyPanel />);

    expect(await screen.findByText("T+N 决策复盘")).toBeInTheDocument();
    expect((await screen.findAllByText("66.7%")).length).toBeGreaterThan(0);
    expect(screen.getByText(/不进入自动调参/)).toBeInTheDocument();
    expect(screen.queryByText(/300%/)).not.toBeInTheDocument();
    expect(screen.getByText(/观察\/复核 1 条/)).toBeInTheDocument();
    expect(screen.getByText("假设费后正收益")).toBeInTheDocument();
    expect(screen.getByText("合同基准超额")).toBeInTheDocument();
    expect(screen.getByText(/默认 1.5%/)).toBeInTheDocument();
    expect(screen.getByText(/不是平台实际扣费/)).toBeInTheDocument();
    expect(screen.getByText("旧口径历史参考")).toBeInTheDocument();
    expect(screen.getByText("已排除正式 V2 统计")).toBeInTheDocument();
  });

  it("keeps a T+N report-outcomes failure actionable and renders the mature contract after retry", async () => {
    const current: ReportOutcomes = {
      has_baseline: false,
      has_data: false,
      message: "T+1 尚未成熟。",
      by_horizon: {
        "T+1": {
          horizon_trading_days: 1,
          eligible_count: 1,
          mature_count: 0,
          skipped_count: 1,
          hit_count: 0,
          miss_count: 0,
          hit_rate_percent: null,
          coverage_percent: 0,
        },
      },
      items: [],
    };
    apiMocks.fetchReportOutcomes
      .mockRejectedValueOnce(new Error("净值复盘接口超时"))
      .mockResolvedValueOnce(current);

    render(<ReportOutcomesPanel reportId="report-1" embedded />);

    expect(await screen.findByRole("alert")).toHaveTextContent("T+N 复盘加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect((await screen.findAllByText("T+1 尚未成熟。")).length).toBeGreaterThan(0);
    await waitFor(() => expect(apiMocks.fetchReportOutcomes).toHaveBeenCalledTimes(2));
  });

  it("does not silently hide a shadow-digest request failure", async () => {
    const digest: ShadowEscalationDigest = {
      available: true,
      escalation_mode: "shadow",
      lookback_days: 7,
      report_count: 2,
      discovery_report_count: 1,
      trigger_count: 0,
      summary: "近 7 天未触发任何灰度升级判定。",
    };
    apiMocks.fetchShadowEscalationDigest
      .mockRejectedValueOnce(new Error("诊断服务超时"))
      .mockResolvedValueOnce(digest);

    render(<ShadowEscalationDigestCard />);

    expect(await screen.findByRole("alert")).toHaveTextContent("灰度复盘加载失败：诊断服务超时");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("近 7 天未触发任何灰度升级判定。")).toBeInTheDocument();
  });

  it("stops after an evidence-overview failure and retries only on request", async () => {
    const emptyEvidence: PortfolioEvidenceOverview = {
      available: false,
      overview: { available: false },
      holdings: [],
    };
    apiMocks.fetchPortfolioEvidenceOverview
      .mockRejectedValueOnce(new Error("证据聚合服务超时"))
      .mockResolvedValueOnce(emptyEvidence);

    render(<PortfolioEvidenceOverviewPanel enabled />);

    expect(await screen.findByRole("alert")).toHaveTextContent("证据总览加载失败");
    expect(apiMocks.fetchPortfolioEvidenceOverview).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(
      await screen.findByText("暂无足够量化证据可聚合（需因子、信号或风险至少一路覆盖）。"),
    ).toBeInTheDocument();
    expect(apiMocks.fetchPortfolioEvidenceOverview).toHaveBeenCalledTimes(2);
  });
});
