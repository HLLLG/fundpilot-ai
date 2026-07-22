"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { NavHistoryListModal } from "@/components/NavHistoryListModal";
import { NavHistoryTable } from "@/components/NavHistoryTable";
import { PerformanceReturnChart, type TradeMarker } from "@/components/PerformanceReturnChart";
import type { FundNavHistory, FundTransaction, IndexDailyHistory } from "@/lib/api";
import {
  fetchFundNavHistory,
  fetchIndexDailyHistory,
  getFundTransactions,
} from "@/lib/api";
import { buildClientCacheKey, readClientCache, writeClientCache } from "@/lib/clientCache";
import {
  buildPerformanceSeries,
  cnSignedPercent,
  formatSignedPercent,
  PERFORMANCE_PERIODS,
} from "@/lib/performanceTrend";

const PREVIEW_LIMIT = 22;
const NAV_HISTORY_TTL_MS = 24 * 60 * 60 * 1000;
const INDEX_DAILY_TTL_MS = 60 * 60 * 1000;

function buildPreviewSeries(points: FundNavHistory["points"]) {
  return points.slice(-PREVIEW_LIMIT).map((point) => ({
    date: point.date.slice(0, 10),
    nav: point.nav,
    dailyReturn: point.daily_return_percent ?? null,
    fundPercent: 0,
    benchPercent: null,
  }));
}

function fundHistoryForPeriod(
  history: FundNavHistory | null | undefined,
  fundCode: string,
  days: number,
  coverageDays: number,
): FundNavHistory | null {
  if (
    !history ||
    history.fund_code !== fundCode ||
    history.points.length === 0 ||
    coverageDays < days
  ) {
    return null;
  }
  const points = history.points.slice(-days);
  const first = points[0];
  const latest = points.at(-1);
  const periodChange =
    first && latest && first.nav > 0
      ? Math.round((latest.nav / first.nav - 1) * 10000) / 100
      : null;
  return {
    ...history,
    points,
    latest_nav: latest?.nav ?? history.latest_nav,
    latest_date: latest?.date ?? history.latest_date,
    period_change_percent: periodChange,
  };
}

type PerformanceTrendPanelProps = {
  fundCode: string;
  fundName: string;
  costPrice?: number | null;
  enabled?: boolean;
  benchmarkSymbol?: string | null;
  benchmarkName?: string | null;
  showTransactions?: boolean;
  initialFundHistory?: FundNavHistory | null;
  initialFundHistoryCoverageDays?: number;
  chartHeight?: number;
};

export function PerformanceTrendPanel({
  fundCode,
  fundName,
  costPrice,
  enabled = true,
  benchmarkSymbol = "000300",
  benchmarkName,
  showTransactions = true,
  initialFundHistory,
  initialFundHistoryCoverageDays = 0,
  chartHeight = 220,
}: PerformanceTrendPanelProps) {
  const [days, setDays] = useState(63);
  const [fundHistory, setFundHistory] = useState<FundNavHistory | null>(null);
  const [benchHistory, setBenchHistory] = useState<IndexDailyHistory | null>(null);
  const [fundLoading, setFundLoading] = useState(false);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);
  const [fundError, setFundError] = useState<string | null>(null);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [transactions, setTransactions] = useState<FundTransaction[]>([]);

  useEffect(() => {
    if (!enabled || !showTransactions || fundCode === "000000") {
      setTransactions([]);
      return;
    }
    let cancelled = false;
    void getFundTransactions(fundCode)
      .then((res) => {
        if (!cancelled) {
          setTransactions(res.transactions);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTransactions([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, fundCode, showTransactions]);

  const tradeMarkers = useMemo<TradeMarker[]>(() => {
    const byDate = new Map<string, FundTransaction[]>();
    for (const tx of transactions) {
      if (!tx.confirm_date) {
        continue;
      }
      const list = byDate.get(tx.confirm_date) ?? [];
      list.push(tx);
      byDate.set(tx.confirm_date, list);
    }
    return Array.from(byDate.entries()).map(([date, txs]) => {
      const hasConfirmed = txs.some(
        (tx) => tx.status === "confirmed" || tx.status === "superseded",
      );
      let kind: TradeMarker["kind"];
      if (!hasConfirmed) {
        kind = "pending";
      } else {
        const net = txs.reduce((acc, tx) => {
          if (tx.status !== "confirmed" && tx.status !== "superseded") {
            return acc;
          }
          return acc + (tx.direction === "buy" ? tx.amount_yuan : -tx.amount_yuan);
        }, 0);
        kind = net >= 0 ? "buy" : "sell";
      }
      return {
        date,
        kind,
        items: txs.map((tx) => ({
          direction: tx.direction,
          amount_yuan: tx.amount_yuan,
          trade_time: tx.trade_time,
          status: tx.status,
        })),
      };
    });
  }, [transactions]);

  useEffect(() => {
    if (!enabled || fundCode === "000000") {
      return;
    }
    let cancelled = false;
    const fundCacheKey = buildClientCacheKey("fund-nav-history", fundCode, days);
    const benchCacheKey = buildClientCacheKey("index-daily", benchmarkSymbol ?? "none", days);
    const cachedFund = readClientCache<FundNavHistory>(fundCacheKey, NAV_HISTORY_TTL_MS);
    const cachedBench = benchmarkSymbol
      ? readClientCache<IndexDailyHistory>(benchCacheKey, INDEX_DAILY_TTL_MS)
      : null;
    const initialFund = fundHistoryForPeriod(
      initialFundHistory,
      fundCode,
      days,
      initialFundHistoryCoverageDays,
    );
    const availableFund = cachedFund ?? initialFund;

    if (initialFund && !cachedFund) {
      writeClientCache(fundCacheKey, initialFund);
    }
    setFundHistory(availableFund);
    setBenchHistory(cachedBench ?? null);
    setFundLoading(!availableFund);
    setBenchmarkLoading(Boolean(benchmarkSymbol) && !cachedBench);
    setFundError(null);
    setBenchmarkError(null);

    if (!availableFund) {
      void fetchFundNavHistory(fundCode, days)
        .then((result) => {
          if (cancelled) return;
          writeClientCache(fundCacheKey, result);
          setFundHistory(result);
        })
        .catch((reason: unknown) => {
          if (cancelled) return;
          setFundError(reason instanceof Error ? reason.message : "加载基金走势失败");
        })
        .finally(() => {
          if (!cancelled) setFundLoading(false);
        });
    }

    if (benchmarkSymbol && !cachedBench) {
      void fetchIndexDailyHistory(benchmarkSymbol, days)
        .then((result) => {
          if (cancelled) return;
          writeClientCache(benchCacheKey, result);
          setBenchHistory(result);
        })
        .catch((reason: unknown) => {
          if (cancelled) return;
          setBenchmarkError(reason instanceof Error ? reason.message : "参考基准暂不可用");
        })
        .finally(() => {
          if (!cancelled) setBenchmarkLoading(false);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [
    benchmarkSymbol,
    days,
    enabled,
    fundCode,
    initialFundHistory,
    initialFundHistoryCoverageDays,
  ]);

  const activeFundHistory = fundHistory?.fund_code === fundCode ? fundHistory : null;
  const series = useMemo(
    () => buildPerformanceSeries(activeFundHistory?.points ?? [], benchHistory?.points ?? []),
    [activeFundHistory?.points, benchHistory?.points],
  );
  const previewSeries = useMemo(
    () => buildPreviewSeries(activeFundHistory?.points ?? []),
    [activeFundHistory?.points],
  );
  const previewLoading = fundLoading && fundHistory?.fund_code !== fundCode;

  const fundPeriodChange = activeFundHistory?.period_change_percent ?? series.at(-1)?.fundPercent ?? null;
  const benchPeriodChange = series.at(-1)?.benchPercent ?? benchHistory?.period_change_percent ?? null;
  const hasBenchmark = Boolean(benchmarkSymbol) && series.some((point) => point.benchPercent != null);

  if (!enabled) {
    return (
      <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
        正在匹配基金代码，或请上传详情页 OCR 补全
      </div>
    );
  }

  return (
    <div className="space-y-0">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-1 py-2 text-[12px]">
        <div className="inline-flex items-center gap-1.5 text-slate-500">
          <span className="h-0.5 w-4 rounded-full bg-[#3d7eff]" />
          <span>本基金</span>
          <span className={`font-semibold tabular-nums ${cnSignedPercent(fundPeriodChange)}`}>
            {formatSignedPercent(fundPeriodChange)}
          </span>
        </div>
        {benchmarkSymbol ? (
          <div className="inline-flex items-center gap-1.5 text-slate-500">
            <span className="h-0.5 w-4 rounded-full bg-[#f59e0b]" />
            <span className="inline-flex items-center gap-0.5">
              {benchHistory?.name ?? benchmarkName ?? benchmarkSymbol}
              <ChevronDown size={12} className="text-slate-500" />
            </span>
            {benchmarkLoading ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-slate-400">
                <Loader2 size={11} className="animate-spin" />加载中
              </span>
            ) : benchmarkError ? (
              <span className="max-w-40 truncate text-[11px] text-slate-400" title={benchmarkError}>
                {benchmarkError}
              </span>
            ) : (
              <span className={`font-semibold tabular-nums ${cnSignedPercent(benchPeriodChange)}`}>
                {formatSignedPercent(benchPeriodChange)}
              </span>
            )}
          </div>
        ) : null}
        {costPrice != null ? (
          <div className="inline-flex items-center gap-1.5 text-slate-500">
            <span className="h-0.5 w-4 rounded-full bg-slate-300" />
            <span>成本价</span>
            <span className="font-semibold tabular-nums text-slate-700">{costPrice.toFixed(4)}</span>
          </div>
        ) : null}
      </div>

      {fundLoading && fundHistory?.fund_code !== fundCode ? (
        <div style={{ height: chartHeight }} className="flex items-center justify-center text-sm text-slate-500">
          <Loader2 size={18} className="mr-2 animate-spin" />
          加载业绩走势…
        </div>
      ) : fundError ? (
        <div className="rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-6 text-center text-sm text-[var(--danger-fg)]">
          {fundError}
        </div>
      ) : series.length >= 2 ? (
        <PerformanceReturnChart
          points={series}
          height={chartHeight}
          showBenchmark={hasBenchmark}
          markers={tradeMarkers}
        />
      ) : (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
          {activeFundHistory?.note ?? "暂无净值历史数据"}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between gap-2 px-1">
        {PERFORMANCE_PERIODS.map((period) => {
          const active = days === period.days;
          return (
            <button
              key={period.label}
              type="button"
              aria-pressed={active}
              onClick={() => setDays(period.days)}
              className={`min-h-11 flex-1 rounded-full px-1 py-2 text-[12px] font-semibold transition ${
                active
                  ? "bg-[#edf3ff] text-[var(--brand-strong)]"
                  : "text-slate-500 hover:bg-slate-50 hover:text-slate-700"
              }`}
            >
              {period.label}
            </button>
          );
        })}
      </div>

      <div className="mt-4 overflow-hidden rounded-xl border border-slate-100 bg-white">
        {previewLoading ? (
          <div className="flex items-center justify-center py-8 text-sm text-slate-500">
            <Loader2 size={16} className="mr-2 animate-spin" />
            加载近1月净值…
          </div>
        ) : (
          <NavHistoryTable points={previewSeries} maxRows={PREVIEW_LIMIT} />
        )}
        <button
          type="button"
          onClick={() => setHistoryOpen(true)}
          className="flex min-h-11 w-full items-center justify-center gap-1 border-t border-slate-100 py-3 text-[12px] font-semibold text-[var(--brand-strong)] hover:bg-slate-50"
        >
          查看历史净值
          <ChevronRight size={14} />
        </button>
      </div>

      <p className="mt-2 px-1 text-center text-[10px] text-slate-500">
        {fundName} · 公开单位净值 · 东财
      </p>

      {historyOpen ? (
        <NavHistoryListModal
          fundCode={fundCode}
          fundName={fundName}
          onClose={() => setHistoryOpen(false)}
        />
      ) : null}
    </div>
  );
}
