// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("job API helpers", () => {
  it("fetchDiscoveryJob reads the shared job-status endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "job-1",
          status: "running",
          job_kind: "discovery",
          created_at: "2026-06-26T00:00:00Z",
          updated_at: "2026-06-26T00:00:01Z",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { fetchDiscoveryJob } = await import("@/lib/api");
    const job = await fetchDiscoveryJob("job-1");

    expect(job.job_kind).toBe("discovery");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/jobs/job-1"),
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("startDiscoveryJob surfaces a readable API error instead of raw JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "当前异步荐基队列已满，请稍后重试" }), {
        status: 429,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { startDiscoveryJob } = await import("@/lib/api");
    await expect(
      startDiscoveryJob([], {
        max_drawdown_percent: 15,
        concentration_limit_percent: 35,
        expected_investment_amount: 30000,
        prefer_dca: true,
        avoid_chasing: true,
        round_trip_fee_percent: 1.5,
        min_net_profit_percent: 1,
        hold_days_target: 7,
      }),
    ).rejects.toThrow("当前异步荐基队列已满，请稍后重试");
  });
});
