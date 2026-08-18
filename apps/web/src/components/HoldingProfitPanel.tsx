"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import { FundHoldingTransactions } from "@/components/FundHoldingTransactions";
import { HoldingProfitHistoryModal } from "@/components/HoldingProfitHistoryModal";
import { HoldingProfitTable } from "@/components/HoldingProfitTable";
import { PerformanceReturnChart } from "@/components/PerformanceReturnChart";
import type {
  DeletePortfolioTransactionResult,
  FundNavHistory,
  FundTransaction,
} from "@/lib/api";
import { fetchFundNavHistory, getFundTransactions } from "@/lib/api";
import {
  cnProfitClass,
  formatPlainMoney,
  formatSignedMoney,
  formatSignedPercent,
} from "@/lib/holdingMetrics";
import {
  buildHoldingProfitSeries,
  inferFirstHoldDate,
  periodProfitChange,
  sliceHoldingProfitSeries,
  toChartSeries,
} from "@/lib/holdingProfitTrend";
import { PERFORMANCE_PERIODS } from "@/lib/performanceTrend";
import { buildTradeMarkers } from "@/lib/tradeMarkers";
import { userFacingErrorMessage } from "@/lib/userFacingError";

const PREVIEW_LIMIT = 22;

type HoldingProfitPanelProps = {
  fundCode: string;
  fundName: string;
  enabled?: boolean;
  shares?: number | null;
  unitCost?: number | null;
  firstHoldDate?: string | null;
  holdingDays?: number | null;
  currentProfit?: number | null;
  currentReturnPercent?: number | null;
  yesterdayProfit?: number | null;
  costBasis?: number | null;
  pendingNote?: string | null;
  refreshKey?: number;
  onDeleteTransaction?: (transactionId: string) => Promise<DeletePortfolioTransactionResult>;
  onTransactionsChanged?: () => void;
};

export function HoldingProfitPanel({
  fundCode,
  fundName,
  enabled = true,
  shares,
  unitCost,
  firstHoldDate,
  holdingDays,
  currentProfit,
  currentReturnPercent,
  yesterdayProfit,
  costBasis,
  pendingNote,
  refreshKey = 0,
  onDeleteTransaction,
  onTransactionsChanged,
}: HoldingProfitPanelProps) {
  const [days, setDays] = useState(63);
  const [fundHistory, setFundHistory] = useState<FundNavHistory | null>(null);
  const [transactions, setTransactions] = useState<FundTransaction[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const resolvedFirstHoldDate = inferFirstHoldDate(firstHoldDate, holdingDays);

  useEffect(() => {
    if (!enabled || fundCode === "000000") {
      setFundHistory(null);
      setTransactions([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    void Promise.all([
      fetchFundNavHistory(fundCode, days),
      getFundTransactions(fundCode).catch(() => ({ transactions: [] as FundTransaction[] })),
    ])
      .then(([history, ledger]) => {
        if (cancelled) {
          return;
        }
        setFundHistory(history);
        setTransactions(ledger.transactions);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(userFacingErrorMessage(reason, "加载持有收益失败"));
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
  }, [days, enabled, fundCode, refreshKey]);

  const series = useMemo(
    () =>
      buildHoldingProfitSeries(fundHistory?.points ?? [], transactions, {
        shares,
        unitCost,
        firstHoldDate: resolvedFirstHoldDate,
        holdingDays,
        currentProfit,
        currentReturnPercent,
      }),
    [
      currentProfit,
      currentReturnPercent,
      fundHistory?.points,
      holdingDays,
      resolvedFirstHoldDate,
      shares,
      transactions,
      unitCost,
    ],
  );
  const windowed = useMemo(() => sliceHoldingProfitSeries(series, days), [days, series]);
  const chartPoints = useMemo(() => toChartSeries(windowed), [windowed]);
  const previewPoints = useMemo(() => windowed.slice(-PREVIEW_LIMIT), [windowed]);
  const tradeMarkers = useMemo(() => buildTradeMarkers(transactions), [transactions]);
  const periodChange = periodProfitChange(windowed);
  const latest = windowed.at(-1);
  const displayProfit = currentProfit ?? latest?.cumulativeProfit ?? null;
  const displayReturn = currentReturnPercent ?? latest?.holdingReturnPercent ?? null;

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
          <span>持有收益</span>
          <span className={`font-semibold tabular-nums ${cnProfitClass(displayProfit)}`}>
            {formatSignedMoney(displayProfit)}
          </span>
          <span className={`font-semibold tabular-nums ${cnProfitClass(displayReturn)}`}>
            {formatSignedPercent(displayReturn)}
          </span>
        </div>
        <div className="inline-flex items-center gap-1.5 text-slate-500">
          <span>本期</span>
          <span className={`font-semibold tabular-nums ${cnProfitClass(periodChange)}`}>
            {formatSignedMoney(periodChange)}
          </span>
        </div>
        {yesterdayProfit != null ? (
          <div className="inline-flex items-center gap-1.5 text-slate-500">
            <span>昨日</span>
            <span className={`font-semibold tabular-nums ${cnProfitClass(yesterdayProfit)}`}>
              {formatSignedMoney(yesterdayProfit)}
            </span>
          </div>
        ) : null}
      </div>

      {loading && fundHistory?.fund_code !== fundCode ? (
        <div className="flex h-[220px] items-center justify-center text-sm text-slate-500">
          <Loader2 size={18} className="mr-2 animate-spin" />
          加载持有收益…
        </div>
      ) : error ? (
        <div className="rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-6 text-center text-sm text-[var(--danger-fg)]">
          {error}
        </div>
      ) : chartPoints.length >= 2 ? (
        <PerformanceReturnChart
          points={chartPoints}
          height={220}
          showBenchmark={false}
          markers={tradeMarkers}
          formatValue={formatSignedMoney}
          emptyLabel="持有时间较短，暂不足绘制收益走势"
          seriesNoun="持有收益"
        />
      ) : (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm leading-6 text-slate-500">
          {fundHistory?.note ??
            "还没有足够的净值点绘制走势。之前的收益没有按日流水记账；设置首次购入日或导入更早成交后，会按净值回补。"}
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
        {loading && previewPoints.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-sm text-slate-500">
            <Loader2 size={16} className="mr-2 animate-spin" />
            加载历史收益…
          </div>
        ) : (
          <HoldingProfitTable points={previewPoints} maxRows={PREVIEW_LIMIT} />
        )}
        <button
          type="button"
          onClick={() => setHistoryOpen(true)}
          disabled={series.length === 0}
          className="flex min-h-11 w-full items-center justify-center gap-1 border-t border-slate-100 py-3 text-[12px] font-semibold text-[var(--brand-strong)] hover:bg-slate-50 disabled:text-slate-400"
        >
          查看历史收益
          <ChevronRight size={14} />
        </button>
      </div>

      <p className="mt-2 px-1 text-center text-[10px] leading-4 text-slate-500">
        {fundName} · 持有收益按成交份额 × 单位净值回放，不是支付宝逐日记账
        {costBasis != null ? ` · 成本总额 ${formatPlainMoney(costBasis)}` : ""}
        。加仓前已有份额会按成本价回补；若起点不对，请设置首次购入日或导入更早交易。
      </p>

      {pendingNote ? (
        <p className="mt-2 rounded-lg bg-[var(--warn-bg)] px-3 py-2 text-[11px] font-semibold leading-5 text-[var(--warn-fg)]">
          {pendingNote}
        </p>
      ) : null}

      <div className="mt-3">
        <FundHoldingTransactions
          fundCode={fundCode}
          enabled={enabled || Boolean(fundCode)}
          refreshKey={refreshKey}
          onDeleteTransaction={onDeleteTransaction}
          onTransactionsChanged={onTransactionsChanged}
        />
      </div>

      {historyOpen ? (
        <HoldingProfitHistoryModal
          fundName={fundName}
          points={series}
          onClose={() => setHistoryOpen(false)}
        />
      ) : null}
    </div>
  );
}
