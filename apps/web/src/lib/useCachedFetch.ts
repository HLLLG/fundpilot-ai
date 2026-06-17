"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type ClientCacheStorage,
  peekClientCacheAgeMs,
  readClientCache,
  writeClientCache,
} from "@/lib/clientCache";

export type UseCachedFetchOptions<T> = {
  cacheKey: string;
  fetcher: () => Promise<T>;
  staleTimeMs: number;
  enabled?: boolean;
  storage?: ClientCacheStorage;
  /** 若新数据不满足此条件且已有旧数据，则保留旧数据（stale-while-revalidate） */
  keepPreviousUnless?: (fresh: T) => boolean;
};

export function useCachedFetch<T>({
  cacheKey,
  fetcher,
  staleTimeMs,
  enabled = true,
  storage = "memory",
  keepPreviousUnless,
}: UseCachedFetchOptions<T>) {
  const fetcherRef = useRef(fetcher);
  const keepPreviousUnlessRef = useRef(keepPreviousUnless);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  useEffect(() => {
    keepPreviousUnlessRef.current = keepPreviousUnless;
  }, [keepPreviousUnless]);

  const [data, setData] = useState<T | null>(() =>
    enabled ? readClientCache<T>(cacheKey, -1, storage) : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(() => enabled && data == null);
  const [revalidating, setRevalidating] = useState(false);

  const revalidate = useCallback(
    async (force = false) => {
      if (!enabled) {
        return;
      }
      const cached = !force ? readClientCache<T>(cacheKey, -1, storage) : null;
      if (cached != null) {
        setData(cached);
        setLoading(false);
      }

      const ageMs = peekClientCacheAgeMs(cacheKey, storage);
      const isFresh = !force && ageMs != null && ageMs <= staleTimeMs;
      if (isFresh) {
        return;
      }

      setRevalidating(cached != null);
      if (cached == null) {
        setLoading(true);
      }
      try {
        const fresh = await fetcherRef.current();
        const acceptFresh =
          keepPreviousUnlessRef.current == null || keepPreviousUnlessRef.current(fresh);
        if (acceptFresh) {
          writeClientCache(cacheKey, fresh, storage);
          setData(fresh);
          setError(null);
        }
      } catch (loadError) {
        if (cached == null) {
          setError(loadError instanceof Error ? loadError.message : "加载失败");
        }
      } finally {
        setLoading(false);
        setRevalidating(false);
      }
    },
    [cacheKey, enabled, staleTimeMs, storage],
  );

  useEffect(() => {
    if (!enabled) {
      return;
    }
    void revalidate(false);
  }, [cacheKey, enabled, revalidate]);

  const refresh = useCallback(() => revalidate(true), [revalidate]);

  return { data, error, loading, revalidating, refresh, setData };
}
