// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Holding } from "@/lib/api";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: { id: 1 } }),
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
    fetchHoldingDetail: vi.fn(async (payload) => ({
      index: payload.index,
      holding: payload.holdings[payload.index],
      fund_code_resolved: true,
      provenance: {},
    })),
    fetchSectorIntraday: vi.fn(),
    updateFundProfile: vi.fn(),
    updateFundProfilePurchaseDate: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeHolding(code: string, name: string): Holding {
  return {
    fund_code: code,
    fund_name: name,
    holding_amount: 1000,
    return_percent: 1,
  };
}

describe("YangjibaoFundDetail navigation", () => {
  it("wraps from last fund to first on next", () => {
    const holdings = [
      makeHolding("008586", "华夏人工智能ETF联接C"),
      makeHolding("015945", "易方达国防军工混合C"),
    ];
    const onNavigate = vi.fn();

    render(
      <YangjibaoFundDetail
        holding={holdings[1]}
        holdingIndex={1}
        holdings={holdings}
        onClose={vi.fn()}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "下一只" }));

    expect(onNavigate).toHaveBeenCalledWith({
      fund_code: "008586",
      fund_name: "华夏人工智能ETF联接C",
    });
  });
});
