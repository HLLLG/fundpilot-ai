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



  it("persists amounts but strips sector quote fields", () => {

    const refreshedAt = new Date().toISOString();

    saveCachedPortfolioHoldings({

      holdings: [

        {

          fund_code: "519674",

          fund_name: "银河创新成长",

          holding_amount: 1000,

          return_percent: 1,

          sector_name: "半导体",

          sector_return_percent: 3.2,

          daily_profit: 32,

        },

      ],

      portfolio_summary: {

        total_assets: 1000,

        daily_profit: 10,

        holding_count: 1,

      },

      refreshed_at: refreshedAt,

    });



    const cached = loadCachedPortfolioHoldings();

    expect(cached?.holdings[0].holding_amount).toBe(1000);

    expect(cached?.holdings[0].sector_return_percent).toBeUndefined();

    expect(cached?.holdings[0].sector_name).toBeUndefined();

    expect(cached?.holdings[0].daily_profit).toBeUndefined();

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

