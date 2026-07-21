// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { FundDisclosureHolding, Holding } from "@/lib/api";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: { id: 21 } }),
}));

vi.mock("@/lib/tradingSessionClient", () => ({
  hydrateTradingSession: vi.fn(() => () => undefined),
}));

vi.mock("@/lib/holdingDetailCache", () => ({
  readHoldingDetailCache: vi.fn(() => null),
  readIntradayCache: vi.fn(() => null),
  writeHoldingDetailCache: vi.fn(),
  writeIntradayCache: vi.fn(),
}));

const disclosureRows: FundDisclosureHolding[] = [
  ["688012", "中微公司", 15, 0.6, 18.83, "decreased", -5],
  ["002371", "北方华创", 13, 0.52, 10, "increased", 1.2],
  ["688072", "拓荆科技", 7.5, 0.3, 20, "new", null],
  ["300604", "长川科技", 6.5, 0.26, -2.1, "unchanged", 0],
  ["688120", "华海清科", 5.25, 0.21, null, "increased", 0.5],
  ["688126", "沪硅产业", 3.75, 0.15, 9.92, "decreased", -0.4],
].map(([code, name, display, nav, quote, direction, change], index) => ({
  rank: index + 1,
  security_code: String(code),
  security_name: String(name),
  security_market: "CN",
  quote_change_percent: quote == null ? null : Number(quote),
  display_weight_percent: Number(display),
  nav_weight_percent: Number(nav),
  display_weight_basis: "stock_position",
  previous_nav_weight_percent: null,
  previous_display_weight_percent: null,
  change_percent_points: change == null ? null : Number(change),
  change_direction: direction as FundDisclosureHolding["change_direction"],
  comparison_basis: "stock_position",
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchHoldingDetail: vi.fn(async (payload) => ({
      index: payload.index,
      holding: payload.holdings[payload.index],
      fund_code_resolved: true,
      provenance: {},
    })),
    fetchSectorIntraday: vi.fn(async () => ({
      source_type: "concept",
      source_name: "人工智能",
      points: [],
      close_change_percent: 7.59,
    })),
    fetchFundHoldingsDistribution: vi.fn(async () => ({
      fund_code: "008586",
      status: "available",
      report_period: "2026-Q1",
      as_of_date: "2026-03-31",
      disclosed_at: "2026-04-23T00:00:00+08:00",
      freshness: "fresh",
      previous_report_period: "2025-Q3",
      previous_as_of_date: "2025-09-30",
      display_weight_basis: "stock_position",
      stock_allocation_percent: 4,
      disclosed_weight_percent: 2.62,
      holdings: disclosureRows,
      source: "test",
      allocation_source: "test",
      quote_session_date: "2026-07-21",
      quote_updated_at: "2026-07-21T15:00:00+08:00",
      quote_source: "test",
      data_note: "股票仓位内占比按季报股票仓位归一化；占基金净值为官方披露口径。",
      generated_at: "2026-07-21T12:00:00+08:00",
      reason_codes: [],
    })),
    updateFundProfile: vi.fn(),
    updateFundProfilePurchaseDate: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const holding: Holding = {
  fund_code: "008586",
  fund_name: "华夏人工智能ETF联接C",
  holding_amount: 6795.33,
  return_percent: 7.11,
  sector_name: "人工智能",
  sector_return_percent: 7.59,
};

describe("YangjibaoFundDetail quarterly holdings", () => {
  it("renders the compact disclosure directly below the related-sector summary", async () => {
    render(
      <YangjibaoFundDetail
        holding={holding}
        holdingIndex={0}
        holdings={[holding]}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const holdingsTitle = await screen.findByRole("heading", { name: "上季持仓" });
    const relationSummaries = screen.getAllByText("关联板块");
    const relationSummary = relationSummaries[relationSummaries.length - 1];
    expect(
      relationSummary.compareDocumentPosition(holdingsTitle) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      screen.queryByText("板块/指数涨幅仅作行情参考，不等同基金官方净值涨幅"),
    ).not.toBeInTheDocument();

    expect(await screen.findByText("2026 一季报")).toBeInTheDocument();
    expect(screen.getByText(/截至 03-31/)).toHaveTextContent("股票仓位 4.00%");
    expect(screen.getByText("15.00%")).toBeInTheDocument();
    expect(screen.getByText("净值 0.60%")).toBeInTheDocument();
    expect(screen.getByText("07-21涨幅")).toBeInTheDocument();
    expect(screen.getByText("+18.83%")).toHaveClass("text-rose-600");
    expect(screen.getByText("-2.10%")).toHaveClass("text-emerald-600");
    expect(screen.getByText("较25Q3")).toBeInTheDocument();
    expect(screen.getByText("华海清科")).toBeInTheDocument();
    expect(screen.queryByText("沪硅产业")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看全部 6 只" }));
    expect(screen.getByText("沪硅产业")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起持仓" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
