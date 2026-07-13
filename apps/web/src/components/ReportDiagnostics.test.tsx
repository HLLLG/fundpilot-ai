// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { Holding, InvestorProfile } from "@/lib/api";

const panelSpies = vi.hoisted(() => ({
  breadth: vi.fn(),
  news: vi.fn(),
  recommendation: vi.fn(),
  sectorBacktest: vi.fn(),
  shadow: vi.fn(),
}));

vi.mock("@/components/MarketBreadthGauge", () => ({
  MarketBreadthGauge: ({ compact }: { compact?: boolean }) => {
    panelSpies.breadth({ compact });
    return <div data-testid="breadth-panel" />;
  },
}));
vi.mock("@/components/NewsPreviewPanel", () => ({
  NewsPreviewPanel: ({
    holdings,
    profile,
  }: {
    holdings: Holding[];
    profile: InvestorProfile;
  }) => {
    panelSpies.news({ holdings, profile });
    return <div data-testid="news-panel" />;
  },
}));
vi.mock("@/components/RecommendationAccuracyPanel", () => ({
  RecommendationAccuracyPanel: () => {
    panelSpies.recommendation();
    return <div data-testid="recommendation-panel" />;
  },
}));
vi.mock("@/components/SectorSignalBacktestPanel", () => ({
  SectorSignalBacktestPanel: ({ sectorLabels }: { sectorLabels?: string[] }) => {
    panelSpies.sectorBacktest({ sectorLabels });
    return <div data-testid="sector-backtest-panel" />;
  },
}));
vi.mock("@/components/ShadowEscalationDigestCard", () => ({
  ShadowEscalationDigestCard: () => {
    panelSpies.shadow();
    return <div data-testid="shadow-panel" />;
  },
}));

import { ReportDiagnostics } from "@/components/ReportDiagnostics";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function holding(fundCode: string, sectorName: string | null): Holding {
  return {
    fund_code: fundCode,
    fund_name: `基金 ${fundCode}`,
    holding_amount: 1_000,
    return_percent: 0,
    sector_name: sectorName,
  };
}

it("preserves diagnostic props and sends unique trimmed sector labels to backtesting", () => {
  const holdings = [
    holding("000001", " 半导体 "),
    holding("000002", "半导体"),
    holding("000003", null),
    holding("000004", " 医药 "),
  ];
  const profile: InvestorProfile = {
    style: "稳健",
    horizon: "半年到一年",
    max_drawdown_percent: 8,
    concentration_limit_percent: 35,
    prefer_dca: true,
    avoid_chasing: true,
  };

  render(<ReportDiagnostics holdings={holdings} profile={profile} />);

  expect(screen.getByTestId("diagnostics-content")).toBeInTheDocument();
  expect(screen.getByTestId("breadth-panel")).toBeInTheDocument();
  expect(screen.getByTestId("shadow-panel")).toBeInTheDocument();
  expect(screen.getByTestId("news-panel")).toBeInTheDocument();
  expect(screen.getByTestId("recommendation-panel")).toBeInTheDocument();
  expect(screen.getByTestId("sector-backtest-panel")).toBeInTheDocument();
  expect(panelSpies.breadth).toHaveBeenCalledWith({ compact: true });
  expect(panelSpies.news).toHaveBeenCalledWith({ holdings, profile });
  expect(panelSpies.sectorBacktest).toHaveBeenCalledWith({
    sectorLabels: ["半导体", "医药"],
  });
});
