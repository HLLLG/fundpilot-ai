"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Plus, RefreshCw, Settings2 } from "lucide-react";
import type { Holding, PortfolioSummary } from "@/lib/api";
import { holdingRelatedBoardLabel } from "@/lib/profileSector";
import { SectorMappingModal } from "@/components/SectorMappingModal";
import {
  cnProfitClass,
  computeDailyProfit,
  computeHoldingProfit,
  formatSignedMoney,
  formatSignedPercent,
  holdingProfitIsEstimated,
  resolveHoldingReturnPercent,
  sumDailyProfit,
  sumHoldingAmount,
  withoutTestHoldings,
} from "@/lib/holdingMetrics";
import type { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";

type SectorRefreshControl = ReturnType<typeof useSectorQuoteRefresh>;

type YangjibaoHoldingsBoardProps = {
  holdings: Holding[];
  portfolioSummary?: PortfolioSummary | null;
  sectorRefresh: SectorRefreshControl;
  isLoading?: boolean;
  className?: string;
  onOpenCapture?: () => void;
  onAddHolding?: () => void;
  onExpandReview?: () => void;
  onSelectHolding?: (index: number) => void;
};

function formatClock(date: Date) {
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function accountDailyReturnPercent(
  dailyProfit: number | null,
  totalAssets: number | null,
): number | null {
  if (dailyProfit == null || totalAssets == null || totalAssets <= 0) {
    return null;
  }
  const previousTotal = totalAssets - dailyProfit;
  if (previousTotal <= 0) {
    return null;
  }
  return Math.round((dailyProfit / previousTotal) * 10000) / 100;
}

function sectorLiveLabel(meta: SectorRefreshControl["sectorMetaByIndex"][number]) {
  if (!meta) {
    return null;
  }
  if (meta.source === "live") {
    return "实时";
  }
  if (meta.confidence === "low") {
    return "待选";
  }
  return null;
}

export function YangjibaoHoldingsBoard({
  holdings,
  portfolioSummary,
  sectorRefresh,
  isLoading = false,
  className,
  onOpenCapture,
  onAddHolding,
  onExpandReview,
  onSelectHolding,
}: YangjibaoHoldingsBoardProps) {
  const [now, setNow] = useState(() => new Date());
  const {
    isRefreshing,
    sectorMetaByIndex,
    lastFetchedAt,
    refreshError,
    autoRefreshEnabled,
    autoIntervalMs,
    mappingQueue,
    refresh,
    selectMapping,
    dismissMapping,
    toggleAutoRefresh,
  } = sectorRefresh;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const displayHoldings = useMemo(() => withoutTestHoldings(holdings), [holdings]);

  const computedTotal = sumHoldingAmount(displayHoldings);
  const computedDaily = sumDailyProfit(displayHoldings);
  const totalAssets =
    computedTotal ||
    portfolioSummary?.total_assets ||
  null;
  const dailyProfit = displayHoldings.length > 0 ? computedDaily : null;
  const dailyReturn = accountDailyReturnPercent(dailyProfit, totalAssets);

  const refreshTimeLabel = lastFetchedAt
    ? new Date(lastFetchedAt).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      })
    : null;

  const sortedHoldings = useMemo(
    () =>
      displayHoldings
        .map((holding) => {
          const index = holdings.findIndex(
            (item) => item.fund_code === holding.fund_code && item.fund_name === holding.fund_name,
          );
          return { holding, index: index >= 0 ? index : 0 };
        })
        .sort((left, right) => right.holding.holding_amount - left.holding.holding_amount),
    [displayHoldings, holdings],
  );

  const sectionClassName = className ?? "max-w-lg";

  if (!displayHoldings.length) {
    return (
      <section className={`mx-auto w-full ${sectionClassName}`}>
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-5 py-8 text-center">
            <p className="text-sm font-bold text-slate-500">账户汇总</p>
            <p className="mt-6 text-3xl font-black text-slate-300">—</p>
            <p className="mt-6 text-sm leading-6 text-slate-400">
              {isLoading
                ? "正在从基金档案恢复持仓并刷新板块涨跌…"
                : "暂无持仓。请先在「基金档案」上传单基金详情截图建档，或在校对表手动录入。"}
            </p>
            {!isLoading && onOpenCapture ? (
              <button
                type="button"
                onClick={onOpenCapture}
                className="mt-5 rounded-full bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700"
              >
                去上传详情截图
              </button>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className={`mx-auto w-full ${sectionClassName}`}>
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.06)]">
        {/* 账户总览 */}
        <div className="border-b border-slate-100 px-5 pb-4 pt-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-3 text-sm font-bold text-slate-800">
              <span className="border-b-2 border-slate-900 pb-0.5">账户汇总</span>
              <span className="text-slate-400">养基宝</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void refresh(false)}
                disabled={isRefreshing}
                className="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 disabled:opacity-50"
                title="刷新板块涨跌"
              >
                <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
              </button>
              <label
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-full bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-600"
                title={`每 ${Math.round(autoIntervalMs / 1000)} 秒自动刷新板块`}
              >
                <input
                  type="checkbox"
                  checked={autoRefreshEnabled}
                  onChange={(event) => toggleAutoRefresh(event.target.checked)}
                  className="h-3 w-3"
                />
                自动
              </label>
            </div>
          </div>

          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[2rem] font-black leading-none tabular-nums tracking-tight text-slate-950">
                {formatMoney(totalAssets)}
              </div>
              <div className="mt-1 text-xs text-slate-400">
                {displayHoldings.length} 只基金
                {refreshError ? (
                  <span className="ml-1 font-semibold text-rose-600" title={refreshError}>
                    · 刷新失败
                  </span>
                ) : refreshTimeLabel ? (
                  <span> · 板块 {refreshTimeLabel}</span>
                ) : null}
              </div>
              {refreshError ? (
                <div className="mt-2 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-700">
                  {refreshError}
                </div>
              ) : null}
            </div>
            <div className="text-right">
              <div className="text-[11px] font-semibold text-slate-400">当日收益</div>
              <div
                className={`mt-0.5 text-lg font-black tabular-nums ${cnProfitClass(dailyProfit)}`}
              >
                {formatSignedMoney(dailyProfit)}
                {dailyReturn != null ? (
                  <span className="ml-1 text-sm font-bold">
                    ({formatSignedPercent(dailyReturn)})
                  </span>
                ) : null}
              </div>
              <div className="mt-0.5 text-[11px] tabular-nums text-slate-400">{formatClock(now)}</div>
            </div>
          </div>
        </div>

        {/* 表头 */}
        <div className="grid grid-cols-[1fr_5.5rem_5.5rem] gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-2.5 text-[11px] font-bold text-slate-500">
          <div className="flex items-center gap-1.5">
            <Settings2 size={12} className="text-slate-400" />
            <span>基金 / 持有金额</span>
          </div>
          <div className="text-right">关联板块</div>
          <div className="text-right">持有收益</div>
        </div>

        {/* 持仓列表 */}
        <ul className="divide-y divide-slate-100">
          {sortedHoldings.map(({ holding, index }) => {
            const daily = computeDailyProfit(holding);
            const holdingProfit = computeHoldingProfit(holding);
            const holdingReturn = resolveHoldingReturnPercent(holding);
            const sectorReturn = holding.sector_return_percent;
            const meta = sectorMetaByIndex[index];
            const liveTag = sectorLiveLabel(meta);

            return (
              <li key={`${holding.fund_code}-${index}`}>
                <button
                  type="button"
                  onClick={() => onSelectHolding?.(index)}
                  className="grid w-full grid-cols-[1fr_5.5rem_5.5rem] gap-2 px-4 py-3.5 text-left transition hover:bg-slate-50 active:bg-slate-100"
                >
                  <div className="min-w-0">
                    <div className="truncate text-[15px] font-bold leading-snug text-slate-900">
                      {holding.fund_name}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
                      <span className="tabular-nums">¥ {formatMoney(holding.holding_amount)}</span>
                      {daily != null ? (
                        <span
                          className={`font-bold tabular-nums ${cnProfitClass(daily)}`}
                          title="由最新板块涨跌估算"
                        >
                          ≈{formatSignedMoney(daily)}
                        </span>
                      ) : null}
                    </div>
                  </div>

                  <div className="text-right">
                    <div
                      className={`text-[15px] font-black tabular-nums ${cnProfitClass(sectorReturn)}`}
                    >
                      {formatSignedPercent(sectorReturn)}
                    </div>
                    <div className="mt-0.5 truncate text-[10px] text-slate-400">
                      {holdingRelatedBoardLabel(holding)}
                      {holding.intraday_index_name ? (
                        <span className="ml-0.5 text-slate-400" title="养基宝以场内指数涨跌估算当日收益">
                          ·{holding.intraday_index_name}
                        </span>
                      ) : null}
                      {meta?.matched_name &&
                      (holding.intraday_index_name || holding.sector_name) &&
                      meta.matched_name !==
                        (holding.intraday_index_name || holding.sector_name) ? (
                        <span className="ml-0.5 text-slate-400" title="行情源名称">
                          →{meta.matched_name}
                        </span>
                      ) : null}
                      {liveTag ? (
                        <span className="ml-1 font-bold text-emerald-600">{liveTag}</span>
                      ) : meta?.confidence === "low" ? (
                        <span className="ml-1 font-bold text-amber-600">待选</span>
                      ) : null}
                    </div>
                  </div>

                  <div className="text-right">
                    <div
                      className={`text-[15px] font-black tabular-nums ${cnProfitClass(holdingProfit)}`}
                    >
                      {formatSignedMoney(holdingProfit)}
                    </div>
                    {holdingReturn != null ? (
                      <div
                        className={`mt-0.5 text-[11px] font-bold tabular-nums ${cnProfitClass(holdingReturn)}`}
                      >
                        {holdingProfitIsEstimated(holding) ? "≈" : ""}
                        ({formatSignedPercent(holdingReturn)})
                      </div>
                    ) : (
                      <div className="mt-0.5 text-[11px] text-slate-300">—</div>
                    )}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>

        {/* 底部操作 */}
        <div className="grid grid-cols-2 gap-px border-t border-slate-100 bg-slate-100">
          <button
            type="button"
            onClick={onAddHolding}
            className="flex items-center justify-center gap-1.5 bg-white py-3.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50"
          >
            <Plus size={16} />
            新增持有
          </button>
          <button
            type="button"
            onClick={onExpandReview}
            className="flex items-center justify-center gap-1.5 bg-white py-3.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50"
          >
            详细校对
          </button>
        </div>
      </div>

      <SectorMappingModal
        open={mappingQueue.length > 0}
        fundName={mappingQueue[0]?.fundName ?? ""}
        sectorName={mappingQueue[0]?.sectorName}
        candidates={mappingQueue[0]?.candidates ?? []}
        onClose={dismissMapping}
        onSelect={(candidate) => void selectMapping(candidate)}
      />
    </section>
  );
}

export function CollapsibleReviewSection({
  open,
  onToggle,
  children,
  warningCount = 0,
}: {
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  warningCount?: number;
}) {
  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={onToggle}
        className="mb-3 flex w-full items-center justify-between gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-left text-sm font-black text-slate-950 shadow-sm transition hover:bg-slate-50"
      >
        <span className="flex items-center gap-2">
          持仓详细校对
          {warningCount > 0 ? (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800">
              {warningCount} 处待核
            </span>
          ) : null}
        </span>
        {open ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
      </button>
      {open ? children : null}
    </div>
  );
}
