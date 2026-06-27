// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("discovery prompt API helper", () => {
  it("deduplicates concurrent prompt fetches", async () => {
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

    const { fetchDiscoveryPrompt } = await import("@/lib/api");
    const [first, second] = await Promise.all([
      fetchDiscoveryPrompt(),
      fetchDiscoveryPrompt(),
    ]);

    expect(first).toEqual(payload);
    expect(second).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
