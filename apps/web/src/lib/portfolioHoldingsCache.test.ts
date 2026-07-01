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

    saveCachedPortfolioHoldings(1, {

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



    const cached = loadCachedPortfolioHoldings(1);

    expect(cached?.holdings[0].holding_amount).toBe(1000);

    expect(cached?.holdings[0].sector_return_percent).toBeUndefined();

    expect(cached?.holdings[0].sector_name).toBeUndefined();

    expect(cached?.holdings[0].daily_profit).toBeUndefined();

  });



  it("persists refreshed_at through cache roundtrip", () => {

    saveCachedPortfolioHoldings(1, {

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

    expect(loadCachedPortfolioHoldings(1)?.refreshed_at).toBe("2026-06-18T08:00:00.000Z");

  });



  it("does not restore holdings cached for another user", () => {

    saveCachedPortfolioHoldings(1, {

      holdings: [

        {

          fund_code: "519674",

          fund_name: "old user fund",

          holding_amount: 1000,

          return_percent: 1,

        },

      ],

    });



    expect(loadCachedPortfolioHoldings(2)).toBeNull();

  });

  it("ignores and clears stale v2 user cache entries", () => {
    window.localStorage.setItem(
      "fundpilot-portfolio-holdings-v2:1",
      JSON.stringify({
        holdings: [
          {
            fund_code: "016665",
            fund_name: "stale v2 fund",
            holding_amount: 100,
            return_percent: 0,
          },
        ],
        fetchedAt: Date.now(),
      }),
    );

    expect(loadCachedPortfolioHoldings(1)).toBeNull();

    saveCachedPortfolioHoldings(1, {
      holdings: [],
      portfolio_summary: { total_assets: 0, holding_count: 0 },
    });

    expect(window.localStorage.getItem("fundpilot-portfolio-holdings-v2:1")).toBeNull();
  });

  it("persists an empty holdings list to overwrite stale deleted holdings", () => {

    saveCachedPortfolioHoldings(1, {

      holdings: [

        {

          fund_code: "016665",

          fund_name: "天弘全球高端制造混合(QDII)C",

          holding_amount: 100,

          return_percent: 0,

        },

      ],

      portfolio_summary: {

        total_assets: 100,

        holding_count: 1,

      },

    });

    saveCachedPortfolioHoldings(1, {

      holdings: [],

      portfolio_summary: {

        total_assets: 0,

        holding_count: 0,

      },

      refreshed_at: "2026-07-01T00:03:12.000Z",

    });

    const cached = loadCachedPortfolioHoldings(1);

    expect(cached).not.toBeNull();

    expect(cached?.holdings).toEqual([]);

    expect(cached?.portfolio_summary?.holding_count).toBe(0);

    expect(cached?.refreshed_at).toBe("2026-07-01T00:03:12.000Z");

  });

});

