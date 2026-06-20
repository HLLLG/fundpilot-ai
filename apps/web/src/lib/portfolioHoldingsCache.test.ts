import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearCachedPortfolioHoldings,
  loadCachedPortfolioHoldings,
  saveCachedPortfolioHoldings,
} from "@/lib/portfolioHoldingsCache";

describe("portfolioHoldingsCache", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    const localStorage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    };
    vi.stubGlobal("window", { localStorage });
  });

  afterEach(() => {
    clearCachedPortfolioHoldings();
    vi.unstubAllGlobals();
  });

  it("persists and restores holdings payload", () => {
    saveCachedPortfolioHoldings({
      holdings: [
        {
          fund_code: "519674",
          fund_name: "银河创新成长",
          holding_amount: 1000,
          return_percent: 1,
          sector_name: "半导体",
        },
      ],
      portfolio_summary: {
        total_assets: 1000,
        daily_profit: 10,
        holding_count: 1,
      },
    });

    const cached = loadCachedPortfolioHoldings();
    expect(cached?.holdings).toHaveLength(1);
    expect(cached?.holdings[0].fund_code).toBe("519674");
    expect(cached?.portfolio_summary?.total_assets).toBe(1000);
    expect(cached?.refreshed_at).toBeNull();
  });

  it("persists refreshed_at through cache roundtrip", () => {
    saveCachedPortfolioHoldings({
      holdings: [
        {
          fund_code: "519674",
          fund_name: "银河创新成长",
          holding_amount: 1000,
          return_percent: 1,
        },
      ],
      refreshed_at: "2026-06-18T08:00:00.000Z",
    });
    expect(loadCachedPortfolioHoldings()?.refreshed_at).toBe("2026-06-18T08:00:00.000Z");
  });
});
