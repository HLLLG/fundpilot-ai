"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Holding,
  HoldingFieldWarning,
  SectorMappingCandidate,
  SectorQuoteMeta,
  SectorQuotesStatus,
} from "@/lib/api";
import { mergeHoldingsPreserveQuoteFields } from "@/lib/holdingMetrics";
import {
  applySectorMapping,
  fetchSectorQuotesStatus,
  refreshSectorQuotes,
  type RefreshSectorQuotesResult,
} from "@/lib/api";
import { userFacingErrorMessage } from "@/lib/userFacingError";
import { startVisibilityAwarePolling } from "@/lib/visibilityPolling";

const FALLBACK_AUTO_INTERVAL_MS = 180_000;
const MIN_AUTO_INTERVAL_MS = 60_000;

export function shouldAutoRefreshHoldingsQuotes(
  status: Pick<SectorQuotesStatus, "enabled" | "auto_refresh_allowed">,
  reason: "interval" | "visible",
): boolean {
  if (!status.enabled) {
    return false;
  }
  if (reason === "visible") {
    return true;
  }
  return status.auto_refresh_allowed;
}

function autoRefreshIntervalMs(status: Pick<SectorQuotesStatus, "auto_interval_seconds">): number {
  return Math.max(MIN_AUTO_INTERVAL_MS, status.auto_interval_seconds * 1000);
}

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
};

export function useSectorQuoteRefresh({
  holdings,
  onChange,
  warnings = [],
  onWarningsChange,
}: UseSectorQuoteRefreshOptions) {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [sectorMetaByFundCode, setSectorMetaByFundCode] = useState<Record<string, SectorQuoteMeta>>({});
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null);
  const [mappingQueue, setMappingQueue] = useState<MappingQueueItem[]>([]);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [lastRefreshResult, setLastRefreshResult] = useState<RefreshSectorQuotesResult | null>(null);
  const holdingsRef = useRef(holdings);
  const warningsRef = useRef(warnings);
  const refreshGenerationRef = useRef(0);
  const inFlightRef = useRef(false);

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
      const nextHoldings = result.ok
        ? result.holdings
        : mergeHoldingsPreserveQuoteFields(holdingsRef.current, result.holdings);
      onChange(nextHoldings);
      const sectorCodes = new Set(["sector_quote_discrepancy"]);
      const kept = warningsRef.current.filter((warning) => !sectorCodes.has(warning.code));
      onWarningsChange?.([...kept, ...(result.holding_warnings ?? [])]);
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
      // 刷新结果不再往外推任何提示：
      //  · 失败已经由 `refreshError` 渲染在总资产下方，也就是它真正描述的那些数字
      //    旁边。两处同时出现时用户会在一屏里读到两遍同一句话。
      //  · "成功但有话要说"的那类信息（走哪条链路取到的行情之类）属于口径说明，
      //    现在由持仓看板上的口径披露承载，不必弹提示。
      return result;
    },
    [onChange, onWarningsChange],
  );

  const refresh = useCallback(
    async (
      forceRefresh = false,
      budget: "fast" | "accurate" = "fast",
      options?: { silent?: boolean },
    ) => {
      if (!holdingsRef.current.length) {
        return undefined;
      }
      const silent = Boolean(options?.silent);
      if (silent && inFlightRef.current) {
        return undefined;
      }
      const generation = ++refreshGenerationRef.current;
      inFlightRef.current = true;
      if (!silent) {
        setIsRefreshing(true);
      }
      try {
        let nextForce = forceRefresh;
        let nextBudget = budget;
        try {
          const status = await fetchSectorQuotesStatus();
          if (
            status.background_sector_refresh_in_flight ||
            status.spot_refresh_in_flight
          ) {
            nextForce = false;
          }
          if (status.official_nav_in_flight && nextBudget === "accurate") {
            nextBudget = "fast";
          }
        } catch {
          // Status probe is best-effort; the refresh endpoint still coalesces.
        }
        const result = await refreshSectorQuotes(holdingsRef.current, {
          forceRefresh: nextForce,
          budget: nextBudget,
        });
        return applyRefreshResult(result, generation);
      } catch (error) {
        if (generation === refreshGenerationRef.current) {
          // 同上：只走行内 refreshError，不再重复弹全局提示。
          setRefreshError(userFacingErrorMessage(error, "刷新板块涨跌失败。"));
        }
        return undefined;
      } finally {
        if (generation === refreshGenerationRef.current) {
          inFlightRef.current = false;
          if (!silent) {
            setIsRefreshing(false);
          }
        }
      }
    },
    [applyRefreshResult],
  );

  const hasHoldings = holdings.length > 0;
  useEffect(() => {
    if (!hasHoldings) {
      return;
    }
    let disposed = false;
    let stopPolling: (() => void) | null = null;

    const pullQuotes = (reason: "interval" | "visible") => {
      void (async () => {
        try {
          const status = await fetchSectorQuotesStatus();
          if (disposed || !shouldAutoRefreshHoldingsQuotes(status, reason)) {
            return;
          }
          await refresh(false, "fast", { silent: true });
        } catch {
          // 自动刷新失败时保留当前数字，手动刷新仍可用。
        }
      })();
    };

    void (async () => {
      let intervalMs = FALLBACK_AUTO_INTERVAL_MS;
      try {
        const status = await fetchSectorQuotesStatus();
        if (disposed) {
          return;
        }
        if (!status.enabled) {
          return;
        }
        intervalMs = autoRefreshIntervalMs(status);
      } catch {
        // 状态接口失败时仍按默认 3 分钟节奏轮询，tick 里会再探一次。
      }
      if (disposed) {
        return;
      }
      stopPolling = startVisibilityAwarePolling({
        intervalMs,
        onTick: pullQuotes,
      });
    })();

    return () => {
      disposed = true;
      stopPolling?.();
    };
  }, [hasHoldings, refresh]);

  const selectMapping = useCallback(
    async (candidate: SectorMappingCandidate) => {
      const current = mappingQueue[0];
      if (!current) {
        return;
      }
      const generation = ++refreshGenerationRef.current;
      inFlightRef.current = true;
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
      } catch {
        // 保存失败时刻意不推进 mappingQueue：映射弹窗因此保持打开、停在同一只基金上，
        // 这本身就是"没成功"的反馈，不需要再叠一条文案。
      } finally {
        if (generation === refreshGenerationRef.current) {
          inFlightRef.current = false;
          setIsRefreshing(false);
        }
      }
    },
    [applyRefreshResult, mappingQueue],
  );

  const dismissMapping = useCallback(() => {
    setMappingQueue((queue) => queue.slice(1));
  }, []);

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
