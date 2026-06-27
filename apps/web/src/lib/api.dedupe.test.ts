// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("bootstrap API dedupe", () => {
  it("deduplicates concurrent investor profile fetches", async () => {
    const payload = { style: "balanced", horizon: "medium" };
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { fetchInvestorProfile } = await import("@/lib/api");
    const [first, second] = await Promise.all([fetchInvestorProfile(), fetchInvestorProfile()]);

    expect(first).toEqual(payload);
    expect(second).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("deduplicates concurrent analysis prompt fetches", async () => {
    const payload = {
      role_prompt: "role",
      default_role_prompt: "default",
      is_custom: true,
    };
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { fetchAnalysisPrompt } = await import("@/lib/api");
    const [first, second] = await Promise.all([fetchAnalysisPrompt(), fetchAnalysisPrompt()]);

    expect(first).toEqual(payload);
    expect(second).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("deduplicates concurrent report list fetches", async () => {
    const payload = [{ id: "r1", title: "日报" }];
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { listReports } = await import("@/lib/api");
    const [first, second] = await Promise.all([listReports(), listReports()]);

    expect(first).toEqual(payload);
    expect(second).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
