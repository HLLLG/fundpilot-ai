"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Holding,
  HoldingFieldWarning,
  SectorMappingCandidate,
  SectorQuoteMeta,
} from "@/lib/api";
import { mergeHoldingsPreserveQuoteFields } from "@/lib/holdingMetrics";
import {
  applySectorMapping,
  fetchSectorQuotesStatus,
  refreshSectorQuotes,
  type RefreshSectorQuotesResult,
} from "@/lib/api";
import { isRoutineSectorRefreshMessage } from "@/lib/sectorQuoteStatus";
const DEFAULT_AUTO_INTERVAL_MS = 180_000;

type MappingQueueItem = {
  index: number;
  fundName: string;
  sectorName?: string | null;
  candidates: SectorMappingCandidate[];
};

type UseSectorQuoteRefreshOptions = {
  holdings: Holding[];
  onChange: (holdings: Holding[]) => void;
  warnings?: HoldingFieldWarning[];
  onWarningsChange?: (warnings: HoldingFieldWarning[]) => void;
  onMessage?: (message: string) => void;
};

export function useSectorQuoteRefresh({
  holdings,
  onChange,
  warnings = [],
  onWarningsChange,
  onMessage,
}: UseSectorQuoteRefreshOptions) {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [sectorMetaByFundCode, setSectorMetaByFundCode] = useState<Record<string, SectorQuoteMeta>>({});
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null);
  const [autoIntervalMs, setAutoIntervalMs] = useState(DEFAULT_AUTO_INTERVAL_MS);
  const [mappingQueue, setMappingQueue] = useState<MappingQueueItem[]>([]);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [lastRefreshResult, setLastRefreshResult] = useState<RefreshSectorQuotesResult | null>(null);
  const holdingsRef = useRef(holdings);
  const warningsRef = useRef(warnings);
  const refreshGenerationRef = useRef(0);

  useEffect(() => {
    holdingsRef.current = holdings;
  }, [holdings]);

  useEffect(() => {
    warningsRef.current = warnings;
  }, [warnings]);

  const invalidatePendingRefresh = useCallback(() => {
    refreshGenerationRef.current += 1;
  }, []);

  const applyRefreshResult = useCallback(
    (result: Awaited<ReturnType<typeof refreshSectorQuotes>>, generation: number) => {
      if (generation !== refreshGenerationRef.current) {
        return undefined;
      }
      onChange(mergeHoldingsPreserveQuoteFields(holdingsRef.current, result.holdings));
      if (result.holding_warnings?.length) {
        const sectorCodes = new Set(["sector_quote_discrepancy"]);
        const kept = warningsRef.current.filter((warning) => !sectorCodes.has(warning.code));
        onWarningsChange?.([...kept, ...result.holding_warnings]);
      }
      const metaMap: Record<string, SectorQuoteMeta> = {};
      const pending: MappingQueueItem[] = [];
      for (const item of result.items) {
        if (item.fund_code) {
          metaMap[item.fund_code] = item.sector_quote_meta;
        }
        if (item.mapping_candidates.length > 0) {
          pending.push({
            index: item.index,
            fundName: item.fund_name,
            sectorName: item.sector_name,
            candidates: item.mapping_candidates,
          });
        }
      }
      setSectorMetaByFundCode(metaMap);
      setLastRefreshResult(result);
      if (result.ok) {
        setRefreshError(null);
        setLastFetchedAt(result.fetched_at ?? new Date().toISOString());
      } else {
        setRefreshError(result.message || "板块刷新失败");
      }
      if (pending.length) {
        setMappingQueue((queue) => [...queue, ...pending]);
      }
      if (result.message && !isRoutineSectorRefreshMessage(result.message)) {
        onMessage?.(result.message);
      }
      return result;
    },
    [onChange, onWarningsChange, onMessage],
  );

  const refresh = useCallback(
    async (forceRefresh = false, budget: "fast" | "accurate" = "fast") => {
      if (!holdingsRef.current.length) {
        return undefined;
      }
      const generation = ++refreshGenerationRef.current;
      setIsRefreshing(true);
      try {
        const result = await refreshSectorQuotes(holdingsRef.current, { forceRefresh, budget });
        return applyRefreshResult(result, generation);
      } catch (error) {
        if (generation === refreshGenerationRef.current) {
          const message = error instanceof Error ? error.message : "刷新板块涨跌失败。";
          setRefreshError(message);
          onMessage?.(message);
        }
        return undefined;
      } finally {
        if (generation === refreshGenerationRef.current) {
          setIsRefreshing(false);
        }
      }
    },
    [applyRefreshResult, onMessage],
  );

  const selectMapping = useCallback(
    async (candidate: SectorMappingCandidate) => {
      const current = mappingQueue[0];
      if (!current) {
        return;
      }
      const generation = ++refreshGenerationRef.current;
      setIsRefreshing(true);
      try {
        const result = await applySectorMapping(holdingsRef.current, {
          index: current.index,
          source_type: candidate.source_type,
          source_name: candidate.source_name,
          source_code: candidate.source_code,
        });
        applyRefreshResult(result, generation);
        if (generation === refreshGenerationRef.current) {
          setMappingQueue((queue) => queue.slice(1));
        }
      } catch (error) {
        onMessage?.(error instanceof Error ? error.message : "保存板块映射失败。");
      } finally {
        if (generation === refreshGenerationRef.current) {
          setIsRefreshing(false);
        }
      }
    },
    [applyRefreshResult, mappingQueue, onMessage],
  );

  const dismissMapping = useCallback(() => {
    setMappingQueue((queue) => queue.slice(1));
  }, []);

  useEffect(() => {
    void fetchSectorQuotesStatus()
      .then((status) => setAutoIntervalMs(status.auto_interval_seconds * 1000))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!holdings.length) {
      return;
    }
    let cancelled = false;
    const tick = async () => {
      if (cancelled) {
        return;
      }
      try {
        const status = await fetchSectorQuotesStatus();
        if (!status.auto_refresh_allowed) {
          return;
        }
        await refresh(false, "fast");
      } catch {
        // background refresh errors are non-fatal
      }
    };
    const timer = window.setInterval(() => void tick(), autoIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [autoIntervalMs, holdings.length, refresh]);

  return {
    isRefreshing,
    sectorMetaByFundCode,
    lastFetchedAt,
    mappingQueue,
    refreshError,
    lastRefreshResult,
    refresh,
    selectMapping,
    dismissMapping,
    invalidatePendingRefresh,
    applyServerRefresh: (result: RefreshSectorQuotesResult) =>
      applyRefreshResult(result, refreshGenerationRef.current),
  };
}
