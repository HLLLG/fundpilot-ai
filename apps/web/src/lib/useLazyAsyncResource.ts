"use client";

import { useCallback, useEffect, useState } from "react";


export type LazyAsyncResource<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
};


export function useLazyAsyncResource<T>({
  enabled,
  load,
  errorMessage,
}: {
  enabled: boolean;
  load: () => Promise<T>;
  errorMessage: string;
}): LazyAsyncResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    if (!enabled || data) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    load()
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : errorMessage);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [data, enabled, errorMessage, load, requestVersion]);

  const retry = useCallback(() => {
    setData(null);
    setRequestVersion((value) => value + 1);
  }, []);

  return { data, loading, error, retry };
}
