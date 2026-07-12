// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deleteClientCache, writeClientCache } from "@/lib/clientCache";
import { useCachedFetch } from "@/lib/useCachedFetch";

const CACHE_KEY = "test:dedupe";

describe("useCachedFetch", () => {
  afterEach(() => {
    deleteClientCache(CACHE_KEY, "memory");
    vi.restoreAllMocks();
  });

  it("dedupes concurrent revalidate calls", async () => {
    const fetcher = vi.fn().mockImplementation(
      () =>
        new Promise<string[]>((resolve) => {
          setTimeout(() => resolve(["a"]), 20);
        }),
    );

    const { result } = renderHook(() =>
      useCachedFetch({
        cacheKey: CACHE_KEY,
        fetcher,
        staleTimeMs: 60_000,
      }),
    );

    await waitFor(() => {
      expect(result.current.data).toEqual(["a"]);
    });

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("skips network when cache is still fresh", async () => {
    const fetcher = vi.fn().mockResolvedValue(["fresh"]);

    const { result, rerender } = renderHook(() =>
      useCachedFetch({
        cacheKey: CACHE_KEY,
        fetcher,
        staleTimeMs: 60_000,
      }),
    );

    await waitFor(() => {
      expect(result.current.data).toEqual(["fresh"]);
    });

    fetcher.mockClear();
    rerender();

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps stale data visible while exposing a background refresh failure", async () => {
    writeClientCache(CACHE_KEY, ["cached"], "memory");
    const fetcher = vi.fn().mockRejectedValue(new Error("行情服务超时"));

    const { result } = renderHook(() =>
      useCachedFetch({
        cacheKey: CACHE_KEY,
        fetcher,
        staleTimeMs: -1,
      }),
    );

    expect(result.current.data).toEqual(["cached"]);
    await waitFor(() => {
      expect(result.current.error).toBe("行情服务超时");
    });
    expect(result.current.data).toEqual(["cached"]);
    expect(result.current.loading).toBe(false);
  });
});
