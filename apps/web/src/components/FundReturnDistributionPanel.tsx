"use client";

import { useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";
import {
  fetchFundReturnDistribution,
  fetchTradingSession,
  type FundReturnDistribution,
  type FundReturnDistributionBinKey,
} from "@/lib/api";
import { useCachedFetch } from "@/lib/useCachedFetch";
import {
  loadFundReturnDistributionCache,
  saveFundReturnDistributionCache,
} from "@/lib/storage";

const CACHE_KEY = "diagnostics:fund-return-distribution";
/** 官方净值周末也不会变；盘中更新靠下面的交易日轮询强制刷新。 */
const STALE_MS = 6 * 60 * 60_000;
const TRADING_DAY_POLL_MS = 15 * 60_000;
const SESSION_RECHECK_MS = 30 * 60_000;
const UNAVAILABLE_RETRY_MS = 30_000;
const MAX_UNAVAILABLE_RETRIES = 4;

export type DistributionRefreshSession = {
  is_trading_day?: boolean;
  session_kind?: string;
  calendar_date?: string;
  effective_trade_date?: string;
};

const UPDATE_TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function hasSettledOfficialDistribution(
  session: DistributionRefreshSession | null | undefined,
  data: FundReturnDistribution | null | undefined,
): boolean {
  return Boolean(
    data?.available &&
      data.source_mode === "official_nav" &&
      session?.effective_trade_date &&
      data.as_of_date === session.effective_trade_date,
  );
}

/**
 * 交易日开盘后继续检查，直到当日官方净值就绪。
 * 非交易日 / 盘前：已有上一交易日官方净值就不再请求，避免周末反复拿到同一份收盘数据。
 */
export function shouldRefreshFundReturnDistribution(
  session: DistributionRefreshSession | null | undefined,
  data: FundReturnDistribution | null | undefined,
): boolean {
  if (!session?.effective_trade_date) {
    return false;
  }
  if (hasSettledOfficialDistribution(session, data)) {
    return false;
  }
  const idleSession =
    !session.is_trading_day || session.session_kind === "trading_day_pre_open";
  if (idleSession) {
    return !(data?.available && data.source_mode === "official_nav");
  }
  return true;
}

export function fundReturnDistributionPollMs(
  session: DistributionRefreshSession | null | undefined,
  data: FundReturnDistribution | null | undefined,
): number {
  return shouldRefreshFundReturnDistribution(session, data)
    ? TRADING_DAY_POLL_MS
    : SESSION_RECHECK_MS;
}

export function formatFundReturnDistributionUpdatedAt(
  data: FundReturnDistribution | null | undefined,
): string | null {
  if (!data?.available) {
    return null;
  }
  if (data.source_mode === "official_nav" && data.as_of_date) {
    return `更新：${data.as_of_date} 15:00`;
  }
  const raw = data.as_of_datetime || data.fetched_at || data.as_of_date;
  if (!raw) {
    return null;
  }
  const formatted = formatShanghaiDateTime(raw);
  return formatted ? `更新：${formatted}` : null;
}

function formatShanghaiDateTime(raw: string): string | null {
  const ms = Date.parse(raw);
  if (Number.isFinite(ms)) {
    const parts = UPDATE_TIME_FORMATTER.formatToParts(new Date(ms));
    const pick = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((part) => part.type === type)?.value ?? "";
    return `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}`;
  }
  const dateOnly = raw.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(dateOnly) ? `${dateOnly} 15:00` : null;
}

const BINS: Array<{
  key: FundReturnDistributionBinKey;
  label: string;
  tone: "down" | "flat" | "up";
}> = [
  { key: "le_neg5", label: "≤-5", tone: "down" },
  { key: "neg5_neg3", label: "-5~-3", tone: "down" },
  { key: "neg3_neg1", label: "-3~-1", tone: "down" },
  { key: "neg1_zero", label: "-1~0", tone: "down" },
  { key: "zero", label: "0", tone: "flat" },
  { key: "zero_one", label: "0~1", tone: "up" },
  { key: "one_three", label: "1~3", tone: "up" },
  { key: "three_five", label: "3~5", tone: "up" },
  { key: "ge_five", label: "≥5", tone: "up" },
];

const BAR_TONE = {
  down: "bg-emerald-500",
  flat: "bg-slate-300",
  up: "bg-rose-500",
} as const;

const COUNT_FORMATTER = new Intl.NumberFormat("zh-CN");

function formatCount(value: number | null | undefined): string {
  return COUNT_FORMATTER.format(value ?? 0);
}

function ratio(value: number | null | undefined, total: number): number {
  if (value == null || total <= 0) {
    return 0;
  }
  return Math.max(0, (value / total) * 100);
}

function DistributionContent({ data }: { data: FundReturnDistribution }) {
  const values = BINS.map((bin) => ({ ...bin, count: data.bins?.[bin.key] ?? 0 }));
  const maxCount = Math.max(1, ...values.map((item) => item.count));
  const total = data.valid_count ?? values.reduce((sum, item) => sum + item.count, 0);
  const decline = data.decline_count ?? 0;
  const advance = data.advance_count ?? 0;
  const flat = data.flat_count ?? 0;

  return (
    <>
      <div className="-mx-1 mt-4 overflow-x-auto px-1 pb-1">
        <div className="min-w-[610px]">
          <div
            className="grid h-44 grid-cols-9 items-end gap-2 border-b border-slate-200 px-1"
            aria-label="基金日增长率九档分布"
          >
            {values.map((item) => {
              const height = item.count > 0 ? Math.max(4, (item.count / maxCount) * 100) : 0;
              return (
                <div key={item.key} className="flex h-full min-w-0 flex-col justify-end text-center">
                  <span className="mb-1 text-[11px] font-bold tabular-nums text-slate-700">
                    {formatCount(item.count)}
                  </span>
                  <div className="flex h-[126px] items-end justify-center">
                    <div
                      className={`w-full max-w-10 rounded-t-sm ${BAR_TONE[item.tone]} transition-[height] duration-500`}
                      style={{ height: `${height}%` }}
                      title={`${item.label}%：${formatCount(item.count)}只`}
                    />
                  </div>
                  <span className="mt-2 whitespace-nowrap text-[10px] font-semibold tabular-nums text-slate-500">
                    {item.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <div className="shrink-0 text-emerald-700">
          <span className="text-xs font-bold">下跌</span>{" "}
          <strong className="text-sm font-bold tabular-nums">{formatCount(decline)}</strong>
        </div>
        <div
          className="flex h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-100"
          aria-label={`下跌${formatCount(decline)}只，平盘${formatCount(flat)}只，上涨${formatCount(advance)}只`}
        >
          <span className="bg-emerald-500" style={{ width: `${ratio(decline, total)}%` }} />
          <span className="bg-slate-300" style={{ width: `${ratio(flat, total)}%` }} />
          <span className="bg-rose-500" style={{ width: `${ratio(advance, total)}%` }} />
        </div>
        <div className="shrink-0 text-rose-700">
          <strong className="text-sm font-bold tabular-nums">{formatCount(advance)}</strong>{" "}
          <span className="text-xs font-bold">上涨</span>
        </div>
      </div>
    </>
  );
}

export function FundReturnDistributionPanel() {
  const { data, error, loading, revalidating, refresh } = useCachedFetch<FundReturnDistribution>({
    cacheKey: CACHE_KEY,
    fetcher: fetchFundReturnDistribution,
    staleTimeMs: STALE_MS,
    storage: "session",
    bootstrap: () => loadFundReturnDistributionCache(),
    keepPreviousUnless: (fresh) => Boolean(fresh.available),
  });
  const dataRef = useRef(data);
  const unavailableRetryCountRef = useRef(0);

  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  useEffect(() => {
    if (data?.available && !data.client_cached) {
      saveFundReturnDistributionCache(data);
    }
  }, [data]);

  useEffect(() => {
    if (data == null || data.available) {
      unavailableRetryCountRef.current = 0;
      return;
    }
    if (unavailableRetryCountRef.current >= MAX_UNAVAILABLE_RETRIES) {
      return;
    }
    const timer = window.setTimeout(() => {
      unavailableRetryCountRef.current += 1;
      void refresh();
    }, UNAVAILABLE_RETRY_MS);
    return () => window.clearTimeout(timer);
  }, [data, refresh]);

  useEffect(() => {
    let timer: number | null = null;
    const stop = () => {
      if (timer != null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const scheduleNext = (session?: DistributionRefreshSession | null) => {
      stop();
      timer = window.setTimeout(() => {
        void runTick();
      }, fundReturnDistributionPollMs(session, dataRef.current));
    };
    const runTick = async () => {
      try {
        const session = await fetchTradingSession();
        if (shouldRefreshFundReturnDistribution(session, dataRef.current)) {
          await refresh();
        }
        if (!document.hidden) {
          scheduleNext(session);
        }
      } catch {
        if (
          dataRef.current == null ||
          (dataRef.current.available && dataRef.current.source_mode !== "official_nav")
        ) {
          await refresh();
        }
        if (!document.hidden) {
          scheduleNext(null);
        }
      }
    };
    const handleVisibility = () => {
      if (document.hidden) {
        stop();
        return;
      }
      void runTick();
    };
    if (!document.hidden) {
      void runTick();
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [refresh, data?.source_mode, data?.as_of_date, data?.available]);

  const updatedAt = formatFundReturnDistributionUpdatedAt(data);

  return (
    <section className="section-card min-w-0 max-w-full overflow-hidden p-0">
      <div className="market-board-head flex items-start justify-between gap-3 px-5 pb-3 pt-5">
        <div>
          <p className="ink-label">Breadth</p>
          <h3 className="mt-1 text-lg font-black text-[var(--brand-deep)]">基金涨跌分布</h3>
        </div>
        <p className="inline-flex shrink-0 items-center gap-1.5 pt-1 text-right text-xs text-slate-400">
          {revalidating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : null}
          <span>{updatedAt}</span>
        </p>
      </div>

      <div className="px-5 pb-5">
      {loading && !data ? (
        <div className="mt-5 flex h-44 items-center justify-center rounded-xl bg-[var(--surface-muted)] text-sm text-[var(--muted)]">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          正在读取最新基金涨跌分布…
        </div>
      ) : data?.available ? (
        <DistributionContent data={data} />
      ) : (
        <p className="mt-4 rounded-xl bg-[var(--surface-muted)] px-3 py-3 text-sm leading-6 text-[var(--muted)]" role="status">
          {data?.message ?? "基金官方净值分布暂不可用。"}
        </p>
      )}

      {error ? (
        <p className="mt-3 text-xs font-semibold text-[var(--warn-fg)]" role="status">
          本次更新失败；如有历史结果仍会保留展示。
        </p>
      ) : null}
      </div>
    </section>
  );
}
