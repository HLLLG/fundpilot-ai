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
});
