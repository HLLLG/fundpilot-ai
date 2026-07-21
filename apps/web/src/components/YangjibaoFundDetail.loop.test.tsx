// @vitest-environment jsdom

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

import type { Holding, HoldingDetail, SectorIntradayResult } from "@/lib/api";
import { fetchHoldingDetail, fetchSectorIntraday } from "@/lib/api";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: { id: 7 } }),
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

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchHoldingDetail: vi.fn(),
    fetchSectorIntraday: vi.fn(),
    fetchFundHoldingsDistribution: vi.fn(async () => ({
      fund_code: "018957",
      status: "unavailable",
      freshness: "unknown",
      display_weight_basis: "fund_nav",
      holdings: [],
      source: "test",
      data_note: "暂无",
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

function baseHolding(): Holding {
  return {
    fund_code: "018957",
    fund_name: "CPO feeder",
    holding_amount: 1000,
    return_percent: 1,
    sector_name: "CPO",
    sector_return_percent: 1,
  };
}

function detailFor(holding: Holding): HoldingDetail {
  return {
    index: 0,
    holding,
    fund_code_resolved: true,
    provenance: {},
  };
}

function intradayResult(): SectorIntradayResult {
  return {
    source_type: "concept",
    source_name: "CPO",
    points: [
      { time: "09:30", percent: 1 },
      { time: "10:00", percent: 2.34 },
    ],
    close_change_percent: 2.34,
  };
}

function DetailHarness() {
  const [holdings, setHoldings] = useState<Holding[]>([baseHolding()]);

  return (
    <YangjibaoFundDetail
      holding={holdings[0]}
      holdingIndex={0}
      holdings={holdings}
      portfolioSummary={null}
      onClose={vi.fn()}
      onNavigate={vi.fn()}
      onHoldingResolved={(index, resolved) => {
        setHoldings((current) =>
          current.map((item, itemIndex) => (itemIndex === index ? resolved : item)),
        );
      }}
    />
  );
}

describe("YangjibaoFundDetail request lifecycle", () => {
  it("does not loop detail and intraday requests after intraday close updates the parent holding", async () => {
    vi.mocked(fetchHoldingDetail).mockImplementation(async (payload) =>
      detailFor(payload.holdings[payload.index]),
    );
    vi.mocked(fetchSectorIntraday).mockResolvedValue(intradayResult());

    render(<DetailHarness />);

    await waitFor(() => expect(fetchSectorIntraday).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(fetchHoldingDetail).toHaveBeenCalledTimes(1);
    expect(fetchSectorIntraday).toHaveBeenCalledTimes(1);
  });
});
