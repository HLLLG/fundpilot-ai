"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Holding,
  HoldingFieldWarning,
  SectorMappingCandidate,
  SectorQuoteMeta,
} from "@/lib/api";
import {
  applySectorMapping,
  fetchSectorQuotesStatus,
  refreshSectorQuotes,
} from "@/lib/api";
import { enrichHoldingComputedFields } from "@/lib/holdingMetrics";
import { loadSectorAutoRefresh, saveSectorAutoRefresh } from "@/lib/storage";

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
  enrichComputed?: boolean;
};

export function useSectorQuoteRefresh({
  holdings,
  onChange,
  warnings = [],
  onWarningsChange,
  onMessage,
  enrichComputed = true,
}: UseSectorQuoteRefreshOptions) {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [sectorMetaByIndex, setSectorMetaByIndex] = useState<Record<number, SectorQuoteMeta>>({});
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null);
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true);
  const [autoIntervalMs, setAutoIntervalMs] = useState(120_000);
  const [mappingQueue, setMappingQueue] = useState<MappingQueueItem[]>([]);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const holdingsRef = useRef(holdings);
  const warningsRef = useRef(warnings);

  useEffect(() => {
    holdingsRef.current = holdings;
  }, [holdings]);

  useEffect(() => {
    warningsRef.current = warnings;
  }, [warnings]);

  const applyRefreshResult = useCallback(
    (result: Awaited<ReturnType<typeof refreshSectorQuotes>>) => {
      let nextHoldings = result.holdings;
      if (enrichComputed) {
        nextHoldings = nextHoldings.map((holding) => enrichHoldingComputedFields(holding));
      }
      onChange(nextHoldings);
      if (result.holding_warnings?.length) {
        const sectorCodes = new Set(["sector_quote_discrepancy"]);
        const kept = warningsRef.current.filter((warning) => !sectorCodes.has(warning.code));
        onWarningsChange?.([...kept, ...result.holding_warnings]);
      }
      const metaMap: Record<number, SectorQuoteMeta> = {};
      const pending: MappingQueueItem[] = [];
      for (const item of result.items) {
        metaMap[item.index] = item.sector_quote_meta;
        if (item.mapping_candidates.length > 0) {
          pending.push({
            index: item.index,
            fundName: item.fund_name,
            sectorName: item.sector_name,
            candidates: item.mapping_candidates,
          });
        }
      }
      setSectorMetaByIndex(metaMap);
      if (result.ok) {
        setRefreshError(null);
        setLastFetchedAt(result.fetched_at ?? new Date().toISOString());
      } else {
        setRefreshError(result.message || "板块刷新失败");
      }
      if (pending.length) {
        setMappingQueue((queue) => [...queue, ...pending]);
      }
      onMessage?.(result.message);
      return result;
    },
    [enrichComputed, onChange, onWarningsChange, onMessage],
  );

  const refresh = useCallback(
    async (forceRefresh = false) => {
      if (!holdingsRef.current.length) {
        return;
      }
      setIsRefreshing(true);
      try {
        const result = await refreshSectorQuotes(holdingsRef.current, { forceRefresh });
        applyRefreshResult(result);
      } catch (error) {
        const message = error instanceof Error ? error.message : "刷新板块涨跌失败。";
        setRefreshError(message);
        onMessage?.(message);
      } finally {
        setIsRefreshing(false);
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
      setIsRefreshing(true);
      try {
        const result = await applySectorMapping(holdingsRef.current, {
          index: current.index,
          source_type: candidate.source_type,
          source_name: candidate.source_name,
          source_code: candidate.source_code,
        });
        applyRefreshResult(result);
        setMappingQueue((queue) => queue.slice(1));
      } catch (error) {
        onMessage?.(error instanceof Error ? error.message : "保存板块映射失败。");
      } finally {
        setIsRefreshing(false);
      }
    },
    [applyRefreshResult, mappingQueue, onMessage],
  );

  const dismissMapping = useCallback(() => {
    setMappingQueue((queue) => queue.slice(1));
  }, []);

  const toggleAutoRefresh = useCallback((enabled: boolean) => {
    setAutoRefreshEnabled(enabled);
    saveSectorAutoRefresh(enabled);
  }, []);

  useEffect(() => {
    setAutoRefreshEnabled(loadSectorAutoRefresh(true));
    void fetchSectorQuotesStatus()
      .then((status) => setAutoIntervalMs(status.auto_interval_seconds * 1000))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!autoRefreshEnabled || !holdings.length) {
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
        await refresh(false);
      } catch {
        // background refresh errors are non-fatal
      }
    };
    const timer = window.setInterval(() => void tick(), autoIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [autoRefreshEnabled, autoIntervalMs, holdings.length, refresh]);

  return {
    isRefreshing,
    sectorMetaByIndex,
    lastFetchedAt,
    autoRefreshEnabled,
    autoIntervalMs,
    mappingQueue,
    refreshError,
    refresh,
    selectMapping,
    dismissMapping,
    toggleAutoRefresh,
    applyServerRefresh: applyRefreshResult,
  };
}
