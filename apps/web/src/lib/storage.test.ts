// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import {
  loadFundReturnDistributionCache,
  saveFundReturnDistributionCache,
} from "@/lib/storage";

beforeEach(() => {
  window.localStorage.clear();
});

describe("FundReturnDistribution cache", () => {
  it("round-trips a distribution payload through localStorage", () => {
    const payload = {
      available: true,
      source_mode: "intraday_estimate",
      valid_count: 9,
    } as never;
    saveFundReturnDistributionCache(payload);
    expect(loadFundReturnDistributionCache()).toEqual(payload);
  });

  it("returns null for missing or malformed entries", () => {
    expect(loadFundReturnDistributionCache()).toBeNull();
    window.localStorage.setItem("fundpilot-fund-return-distribution", "{not json");
    expect(loadFundReturnDistributionCache()).toBeNull();
  });

  it("returns null when older than the max age", () => {
    saveFundReturnDistributionCache({ available: true, valid_count: 1 } as never);
    const stale = JSON.stringify({
      fetchedAt: Date.now() - 31 * 60 * 1000,
      data: { available: true },
    });
    window.localStorage.setItem("fundpilot-fund-return-distribution", stale);
    expect(loadFundReturnDistributionCache(30 * 60 * 1000)).toBeNull();
  });
});
