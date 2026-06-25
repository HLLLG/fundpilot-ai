import { describe, expect, it, vi } from "vitest";
import type { Holding } from "@/lib/api";
import { scheduleHoldingsDetailPrefetch } from "@/lib/holdingDetailPrefetch";
import { writeHoldingDetailCache } from "@/lib/holdingDetailCache";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchHoldingDetail: vi.fn(),
  };
});

import { fetchHoldingDetail } from "@/lib/api";

describe("holdingDetailPrefetch", () => {
  it("skips funds with fresh cache and prefetches stale ones", async () => {
    vi.useFakeTimers();
    const holdings: Holding[] = [
      {
        fund_code: "519674",
        fund_name: "银河创新成长",
        holding_amount: 1000,
        return_percent: 10,
      },
      {
        fund_code: "008586",
        fund_name: "华夏人工智能",
        holding_amount: 2000,
        return_percent: 5,
      },
    ];
    writeHoldingDetailCache(1, "519674", {
      index: 0,
      holding: holdings[0],
      fund_code_resolved: true,
      provenance: {},
    });

    vi.mocked(fetchHoldingDetail).mockResolvedValue({
      index: 1,
      holding: holdings[1],
      fund_code_resolved: true,
      provenance: {},
    });

    const cancel = scheduleHoldingsDetailPrefetch({
      userId: 1,
      holdings,
    });

    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchHoldingDetail).toHaveBeenCalledTimes(1);
    expect(fetchHoldingDetail).toHaveBeenCalledWith(
      expect.objectContaining({ index: 1 }),
    );
    cancel();
    vi.useRealTimers();
  });
});
