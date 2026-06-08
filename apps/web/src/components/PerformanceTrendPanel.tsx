"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { NavHistoryListModal } from "@/components/NavHistoryListModal";
import { NavHistoryTable } from "@/components/NavHistoryTable";
import { PerformanceReturnChart } from "@/components/PerformanceReturnChart";
import type { FundNavHistory, IndexDailyHistory } from "@/lib/api";
import { fetchFundNavHistory, fetchFundNavHistoryPage, fetchIndexDailyHistory } from "@/lib/api";
import {
  buildPerformanceSeries,
  cnSignedPercent,
  formatSignedPercent,
  PERFORMANCE_PERIODS,
} from "@/lib/performanceTrend";

const PREVIEW_LIMIT = 22;

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
  const [previewSeries, setPreviewSeries] = useState<ReturnType<typeof buildPerformanceSeries>>([]);
  const [loading, setLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    if (!enabled || fundCode === "000000") {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void Promise.all([
      fetchFundNavHistory(fundCode, days),
      fetchIndexDailyHistory("000300", days),
    ])
      .then(([fund, bench]) => {
        if (cancelled) {
          return;
        }
        setFundHistory(fund);
        setBenchHistory(bench);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载业绩走势失败");
          setFundHistory(null);
          setBenchHistory(null);
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

  useEffect(() => {
    if (!enabled || fundCode === "000000") {
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    void fetchFundNavHistoryPage(fundCode, { limit: PREVIEW_LIMIT })
      .then((page) => {
        if (cancelled) {
          return;
        }
        setPreviewSeries(
          page.points.map((point) => ({
            date: point.date.slice(0, 10),
            nav: point.nav,
            dailyReturn: point.daily_return_percent ?? null,
            fundPercent: 0,
            benchPercent: null,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) {
          setPreviewSeries([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPreviewLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, fundCode]);

  const series = useMemo(
    () => buildPerformanceSeries(fundHistory?.points ?? [], benchHistory?.points ?? []),
    [benchHistory?.points, fundHistory?.points],
  );

  const fundPeriodChange = fundHistory?.period_change_percent ?? series.at(-1)?.fundPercent ?? null;
  const benchPeriodChange = series.at(-1)?.benchPercent ?? benchHistory?.period_change_percent ?? null;
  const hasBenchmark = series.some((point) => point.benchPercent != null);

  if (!enabled) {
    return (
      <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-400">
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
            <ChevronDown size={12} className="text-slate-400" />
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
        <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
          <Loader2 size={18} className="mr-2 animate-spin" />
          加载业绩走势…
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-6 text-center text-sm text-rose-700">
          {error}
        </div>
      ) : series.length >= 2 ? (
        <PerformanceReturnChart points={series} height={220} showBenchmark={hasBenchmark} />
      ) : (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-400">
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
              onClick={() => setDays(period.days)}
              className={`flex-1 rounded-full px-1 py-1.5 text-[12px] font-semibold transition ${
                active
                  ? "bg-[#edf3ff] text-[#3d7eff]"
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
          <div className="flex items-center justify-center py-8 text-sm text-slate-400">
            <Loader2 size={16} className="mr-2 animate-spin" />
            加载近1月净值…
          </div>
        ) : (
          <NavHistoryTable points={previewSeries} maxRows={PREVIEW_LIMIT} />
        )}
        <button
          type="button"
          onClick={() => setHistoryOpen(true)}
          className="flex w-full items-center justify-center gap-1 border-t border-slate-100 py-3 text-[12px] font-semibold text-[#3d7eff] hover:bg-slate-50"
        >
          查看历史净值
          <ChevronRight size={14} />
        </button>
      </div>

      <p className="mt-2 px-1 text-center text-[10px] text-slate-400">
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
