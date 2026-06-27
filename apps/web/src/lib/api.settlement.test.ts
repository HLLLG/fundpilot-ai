// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("official NAV settlement API helper", () => {
  it("settleOfficialNav posts to the settlement endpoint and returns the payload", async () => {
    const payload = {
      ok: true,
      skipped: false,
      settlement_date: "2026-06-26",
      updated_count: 1,
      holdings: [
        {
          fund_code: "001234",
          fund_name: "Test Fund",
          holding_amount: 1000,
          return_percent: 1.2,
          daily_return_percent_source: "official_nav",
        },
      ],
      portfolio_summary: { total_assets: 1000, holding_count: 1 },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { settleOfficialNav } = await import("@/lib/api");
    const result = await settleOfficialNav();

    expect(result).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/portfolio/settle-official-nav"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
