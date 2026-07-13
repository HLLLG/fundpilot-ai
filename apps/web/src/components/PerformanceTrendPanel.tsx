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

type PerformanceTrendPanelProps = {
  fundCode: string;
  fundName: string;
  costPrice?: number | null;
  enabled?: boolean;
};

export function PerformanceTrendPanel({
  fundCode,
  fundName,
  costPrice,
  enabled = true,
}: PerformanceTrendPanelProps) {
  const [days, setDays] = useState(63);
  const [fundHistory, setFundHistory] = useState<FundNavHistory | null>(null);
  const [benchHistory, setBenchHistory] = useState<IndexDailyHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [transactions, setTransactions] = useState<FundTransaction[]>([]);

  useEffect(() => {
    if (!enabled || fundCode === "000000") {
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
  }, [enabled, fundCode]);

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
    const benchCacheKey = buildClientCacheKey("index-daily", "000300", days);
    const cachedFund = readClientCache<FundNavHistory>(fundCacheKey, NAV_HISTORY_TTL_MS);
    const cachedBench = readClientCache<IndexDailyHistory>(benchCacheKey, INDEX_DAILY_TTL_MS);

    setFundHistory(cachedFund ?? null);
    setBenchHistory(cachedBench ?? null);
    setLoading(!cachedFund || !cachedBench);
    setError(null);

    const fundRequest = cachedFund
      ? Promise.resolve(cachedFund)
      : fetchFundNavHistory(fundCode, days);
    const benchRequest = cachedBench
      ? Promise.resolve(cachedBench)
      : fetchIndexDailyHistory("000300", days);

    void Promise.allSettled([fundRequest, benchRequest])
      .then(([fundResult, benchResult]) => {
        if (cancelled) {
          return;
        }

        if (fundResult.status === "fulfilled") {
          writeClientCache(fundCacheKey, fundResult.value);
          setFundHistory(fundResult.value);
        }
        if (benchResult.status === "fulfilled") {
          writeClientCache(benchCacheKey, benchResult.value);
          setBenchHistory(benchResult.value);
        }

        const loadError =
          fundResult.status === "rejected"
            ? fundResult.reason
            : benchResult.status === "rejected" && !cachedFund && !cachedBench
              ? benchResult.reason
              : null;
        if (loadError) {
          setError(loadError instanceof Error ? loadError.message : "加载业绩走势失败");
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
  }, [days, enabled, fundCode]);

  const series = useMemo(
    () => buildPerformanceSeries(fundHistory?.points ?? [], benchHistory?.points ?? []),
    [benchHistory?.points, fundHistory?.points],
  );
  const previewSeries = useMemo(
    () => (fundHistory?.fund_code === fundCode ? buildPreviewSeries(fundHistory.points) : []),
    [fundCode, fundHistory],
  );
  const previewLoading = loading && fundHistory?.fund_code !== fundCode;

  const fundPeriodChange = fundHistory?.period_change_percent ?? series.at(-1)?.fundPercent ?? null;
  const benchPeriodChange = series.at(-1)?.benchPercent ?? benchHistory?.period_change_percent ?? null;
  const hasBenchmark = series.some((point) => point.benchPercent != null);

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
        <div className="inline-flex items-center gap-1.5 text-slate-500">
          <span className="h-0.5 w-4 rounded-full bg-[#f59e0b]" />
          <span className="inline-flex items-center gap-0.5">
            {benchHistory?.name ?? "沪深300"}
            <ChevronDown size={12} className="text-slate-500" />
          </span>
          <span className={`font-semibold tabular-nums ${cnSignedPercent(benchPeriodChange)}`}>
            {formatSignedPercent(benchPeriodChange)}
          </span>
        </div>
        {costPrice != null ? (
          <div className="inline-flex items-center gap-1.5 text-slate-500">
            <span className="h-0.5 w-4 rounded-full bg-slate-300" />
            <span>成本价</span>
            <span className="font-semibold tabular-nums text-slate-700">{costPrice.toFixed(4)}</span>
          </div>
        ) : null}
      </div>

      {loading ? (
        <div className="flex h-[220px] items-center justify-center text-sm text-slate-500">
          <Loader2 size={18} className="mr-2 animate-spin" />
          加载业绩走势…
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-6 text-center text-sm text-rose-700">
          {error}
        </div>
      ) : series.length >= 2 ? (
        <PerformanceReturnChart
          points={series}
          height={220}
          showBenchmark={hasBenchmark}
          markers={tradeMarkers}
        />
      ) : (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
          {fundHistory?.note ?? "暂无净值历史数据"}
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
