// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  window.localStorage.clear();
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

  it("deduplicates report detail, portfolio summary, and dashboard bootstrap", async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      const payload = url.includes("/refresh-and-hydrate")
        ? { portfolio: { holdings: [] } }
        : url.includes("/portfolio/summary")
          ? { total_assets: 100 }
          : { id: "report-1", title: "日报" };
      return Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("@/lib/api");

    await Promise.all([
      api.fetchReportDetail("report-1"),
      api.fetchReportDetail("report-1"),
    ]);
    await Promise.all([
      api.fetchPortfolioSummary(),
      api.fetchPortfolioSummary(),
    ]);
    await Promise.all([
      api.fetchDashboardBootstrap(),
      api.fetchDashboardBootstrap(),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("never shares authenticated GET promises across access tokens during a switch race", async () => {
    type PendingRequest = {
      url: string;
      authorization: string | null;
      resolve: (response: Response) => void;
    };
    const pending: PendingRequest[] = [];
    const fetchMock = vi.fn().mockImplementation(
      (input: string | URL | Request, init?: RequestInit) =>
        new Promise<Response>((resolve) => {
          pending.push({
            url: String(input),
            authorization: new Headers(init?.headers).get("Authorization"),
            resolve,
          });
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = await import("@/lib/api");
    const authenticatedGets: Array<{ path: string; run: () => Promise<unknown> }> = [
      { path: "/api/investor-profile", run: api.fetchInvestorProfile },
      { path: "/api/analysis-prompt", run: api.fetchAnalysisPrompt },
      { path: "/api/discovery-prompt", run: api.fetchDiscoveryPrompt },
      { path: "/api/reports", run: api.listReports },
      { path: "/api/fund-discovery/reports", run: api.listDiscoveryReports },
      { path: "/api/portfolio/holdings", run: api.fetchPortfolioHoldings },
    ];

    const responseBody = (path: string, owner: string) =>
      path.endsWith("/reports") ? [{ owner }] : { owner };
    const resultOwner = (value: unknown): unknown =>
      Array.isArray(value)
        ? (value[0] as { owner?: unknown } | undefined)?.owner
        : (value as { owner?: unknown }).owner;

    for (const { path, run } of authenticatedGets) {
      const requestStart = pending.length;
      window.localStorage.setItem("fundpilot_access_token", "token-a");
      const accountA = run();
      window.localStorage.setItem("fundpilot_access_token", "token-b");
      const accountB = run();

      const [requestA, requestB] = pending.slice(requestStart);
      expect(requestA).toMatchObject({ authorization: "Bearer token-a" });
      expect(requestB).toMatchObject({ authorization: "Bearer token-b" });
      expect(requestA.url).toContain(path);
      expect(requestB.url).toContain(path);

      requestB.resolve(
        new Response(JSON.stringify(responseBody(path, "account-b")), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      requestA.resolve(
        new Response(JSON.stringify(responseBody(path, "account-a")), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

      expect(resultOwner(await accountB)).toBe("account-b");
      expect(resultOwner(await accountA)).toBe("account-a");
    }

    expect(fetchMock).toHaveBeenCalledTimes(authenticatedGets.length * 2);
  });

  it("does not let a late 401 from the previous account clear the new session", async () => {
    let resolveRequest: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise<Response>((resolve) => {
            resolveRequest = resolve;
          }),
      ),
    );
    const { fetchInvestorProfile } = await import("@/lib/api");

    window.localStorage.setItem("fundpilot_access_token", "token-a");
    const previousAccountRequest = fetchInvestorProfile();
    window.localStorage.setItem("fundpilot_access_token", "token-b");
    resolveRequest?.(new Response("unauthorized", { status: 401 }));

    await expect(previousAccountRequest).rejects.toThrow("unauthorized");
    expect(window.localStorage.getItem("fundpilot_access_token")).toBe("token-b");
  });
});
