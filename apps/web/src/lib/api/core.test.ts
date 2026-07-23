// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
  window.localStorage.clear();
});

describe("apiFetch timeout and cancellation", () => {
  it("aborts a request at the global deadline and reports status 408", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_input: string, init?: RequestInit) => {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(init.signal?.reason ?? new DOMException("aborted", "AbortError")),
            { once: true },
          );
        });
      }),
    );
    const { apiFetch } = await import("@/lib/api/core");

    const request = apiFetch("/slow", { timeoutMs: 1_000 });
    const assertion = expect(request).rejects.toMatchObject({
      name: "ApiError",
      status: 408,
    });
    await vi.advanceTimersByTimeAsync(1_000);
    await assertion;
  });

  it("preserves a caller abort instead of misclassifying it as a timeout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_input: string, init?: RequestInit) => {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(init.signal?.reason ?? new DOMException("aborted", "AbortError")),
            { once: true },
          );
        });
      }),
    );
    const { ApiError, apiFetch } = await import("@/lib/api/core");
    const controller = new AbortController();
    const reason = new DOMException("user cancelled", "AbortError");

    const request = apiFetch("/cancelled", {
      signal: controller.signal,
      timeoutMs: 10_000,
    });
    controller.abort(reason);

    await expect(request).rejects.toBe(reason);
    await request.catch((error) => {
      expect(error).not.toBeInstanceOf(ApiError);
    });
  });

  it("allows explicitly long-lived transports to disable the timer", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("ok", { status: 200 })),
    );
    const { apiFetch } = await import("@/lib/api/core");

    await expect(apiFetch("/stream", { timeoutMs: 0 })).resolves.toHaveProperty(
      "status",
      200,
    );
    expect(vi.getTimerCount()).toBe(0);
  });
});
