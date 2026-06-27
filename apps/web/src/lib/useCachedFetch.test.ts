// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deleteClientCache } from "@/lib/clientCache";
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
});
