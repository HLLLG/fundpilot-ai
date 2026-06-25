import { describe, expect, it } from "vitest";
import {
  buildHoldingDetailCacheKey,
  buildIntradayCacheKey,
  HOLDING_DETAIL_STALE_MS,
  isHoldingDetailCacheFresh,
  writeHoldingDetailCache,
} from "@/lib/holdingDetailCache";
import { peekClientCacheAgeMs, readClientCache } from "@/lib/clientCache";

describe("holdingDetailCache", () => {
  it("scopes detail cache by user and fund code", () => {
    expect(buildHoldingDetailCacheKey(7, "008586")).toBe("holding-detail:7:008586");
    expect(buildHoldingDetailCacheKey(null, "008586")).toBe("holding-detail:anon:008586");
  });

  it("builds intraday cache key from query", () => {
    expect(buildIntradayCacheKey({ source_type: "index", source_name: "中证人工智能" })).toBe(
      "sector-intraday:index:中证人工智能",
    );
  });

  it("marks detail cache fresh within stale window", () => {
    const key = buildHoldingDetailCacheKey(1, "008586");
    writeHoldingDetailCache(1, "008586", {
      index: 0,
      holding: {
        fund_code: "008586",
        fund_name: "华夏人工智能ETF联接C",
        holding_amount: 1000,
        return_percent: 0,
      },
      fund_code_resolved: true,
      provenance: {},
    });
    expect(readClientCache(key, -1, "memory")).not.toBeNull();
    expect(isHoldingDetailCacheFresh(1, "008586")).toBe(true);
    const ageMs = peekClientCacheAgeMs(key, "memory");
    expect(ageMs).not.toBeNull();
    expect(ageMs!).toBeLessThanOrEqual(HOLDING_DETAIL_STALE_MS);
  });
});
