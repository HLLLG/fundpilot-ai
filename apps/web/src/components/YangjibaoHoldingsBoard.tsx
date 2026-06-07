"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  Plus,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { fetchTradingSession, type Holding, type PortfolioSummary } from "@/lib/api";
import { sectorQuoteLookupLabel } from "@/lib/profileSector";
import { SectorMappingModal } from "@/components/SectorMappingModal";
import {
  cnProfitClass,
  computeDailyProfit,
  computeEstimatedDailyReturnPercent,
  computeEstimatedHoldingReturnPercent,
  computeHoldingProfit,
  computeYesterdayProfit,
  dailyProfitIsEstimated,
  formatSignedMoney,
  formatSignedPercent,
  holdingProfitIsEstimated,
  resolveSectorBoardReturnPercent,
  sumDailyProfit,
  sumHoldingAmount,
  withoutTestHoldings,
} from "@/lib/holdingMetrics";
import { buildSectorRefreshNotice } from "@/lib/sectorQuoteStatus";
import { loadAmountsHidden, saveAmountsHidden } from "@/lib/storage";
import { formatTradeDateShort } from "@/lib/tradeDateLabel";
import type { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";

type SectorRefreshControl = ReturnType<typeof useSectorQuoteRefresh>;
type HoldingsSortKey = "amount" | "daily" | "sector" | "holding";
type HoldingsSortDir = "desc" | "asc";
type DailyDisplayMode = "amount" | "percent";

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

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatBalance(value: number | null | undefined, hidden: boolean) {
  if (hidden) {
    return "****";
  }
  return formatMoney(value);
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

function holdingsSortValue(holding: Holding, key: HoldingsSortKey): number | null {
  switch (key) {
    case "daily":
      return computeDailyProfit(holding);
    case "sector":
      return resolveSectorBoardReturnPercent(holding);
    case "holding":
      return computeHoldingProfit(holding);
    case "amount":
      return holding.holding_amount;
  }
}

function compareHoldingsBySort(
  left: Holding,
  right: Holding,
  key: HoldingsSortKey,
  dir: HoldingsSortDir,
): number {
  const leftValue = holdingsSortValue(left, key);
  const rightValue = holdingsSortValue(right, key);
  if (leftValue == null && rightValue == null) {
    return 0;
  }
  if (leftValue == null) {
    return 1;
  }
  if (rightValue == null) {
    return -1;
  }
  const diff = leftValue - rightValue;
  return dir === "desc" ? -diff : diff;
}

function SortableColumnHeader({
  label,
  date,
  columnKey,
  activeSortKey,
  sortDir,
  onSort,
}: {
  label: string;
  date: string | null;
  columnKey: Exclude<HoldingsSortKey, "amount">;
  activeSortKey: HoldingsSortKey;
  sortDir: HoldingsSortDir;
  onSort: () => void;
}) {
  const active = activeSortKey === columnKey;

  return (
    <div className="flex items-start justify-end gap-0.5">
      <div className="text-right">
        <div>{label}</div>
        {date ? <div className="mt-0.5 text-[10px] font-semibold text-slate-400 tabular-nums">{date}</div> : null}
      </div>
      <button
        type="button"
        onClick={onSort}
        className={`mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded transition ${
          active ? "text-slate-700" : "text-slate-300 hover:text-slate-500"
        }`}
        title={
          active
            ? sortDir === "desc"
              ? "收益从高到低，点击切换"
              : "收益从低到高，点击切换"
            : `按${label}排序`
        }
        aria-label={`按${label}排序`}
      >
        {active && sortDir === "asc" ? <ArrowUp size={11} strokeWidth={2.5} /> : <ArrowDown size={11} strokeWidth={2.5} />}
      </button>
    </div>
  );
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
  const [quoteTradeDate, setQuoteTradeDate] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<HoldingsSortKey>("amount");
  const [sortDir, setSortDir] = useState<HoldingsSortDir>("desc");
  const [dailyDisplayMode, setDailyDisplayMode] = useState<DailyDisplayMode>("amount");
  const [amountsHidden, setAmountsHidden] = useState(() => loadAmountsHidden());
  const {
    isRefreshing,
    refreshError,
    mappingQueue,
    refresh,
    selectMapping,
    dismissMapping,
    lastRefreshResult,
  } = sectorRefresh;

  useEffect(() => {
    void fetchTradingSession()
      .then((session) => {
        setQuoteTradeDate(formatTradeDateShort(session.effective_trade_date));
      })
      .catch(() => {
        setQuoteTradeDate(null);
      });
  }, []);

  const displayHoldings = useMemo(() => withoutTestHoldings(holdings), [holdings]);
  const refreshNotice = buildSectorRefreshNotice(lastRefreshResult);

  const computedTotal = sumHoldingAmount(displayHoldings);
  const computedDaily = sumDailyProfit(displayHoldings);
  const totalAssets = computedTotal || portfolioSummary?.total_assets || null;
  const dailyProfit = displayHoldings.length > 0 ? computedDaily : null;
  const dailyReturn = accountDailyReturnPercent(dailyProfit, totalAssets);

  const handleSort = (columnKey: Exclude<HoldingsSortKey, "amount">) => {
    if (sortKey === columnKey) {
      setSortDir((current) => (current === "desc" ? "asc" : "desc"));
      return;
    }
    setSortKey(columnKey);
    setSortDir("desc");
  };

  const sortedHoldings = useMemo(
    () =>
      displayHoldings
        .map((holding) => {
          const index = holdings.findIndex(
            (item) => item.fund_code === holding.fund_code && item.fund_name === holding.fund_name,
          );
          return { holding, index: index >= 0 ? index : 0 };
        })
        .sort((left, right) => compareHoldingsBySort(left.holding, right.holding, sortKey, sortDir)),
    [displayHoldings, holdings, sortDir, sortKey],
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
                ? "正在从基金档案恢复持仓，并尝试刷新真实板块涨跌..."
                : "暂无持仓。请先在“基金档案”上传单基金详情截图建档，或在校对表手动录入。"}
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
        <div className="border-b border-slate-100 px-5 pb-4 pt-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <span className="border-b-2 border-slate-900 pb-0.5">账户汇总</span>
              <button
                type="button"
                onClick={() => {
                  setAmountsHidden((current) => {
                    const next = !current;
                    saveAmountsHidden(next);
                    return next;
                  });
                }}
                className="inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                title={amountsHidden ? "显示金额" : "隐藏金额"}
                aria-label={amountsHidden ? "显示金额" : "隐藏金额"}
              >
                {amountsHidden ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
            <button
              type="button"
              onClick={() => void refresh(false)}
              disabled={isRefreshing}
              className="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 disabled:opacity-50"
              title="刷新板块涨跌"
            >
              <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
            </button>
          </div>

          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[2rem] font-black leading-none tabular-nums tracking-tight text-slate-950">
                {formatBalance(totalAssets, amountsHidden)}
              </div>
              {refreshError ? (
                <div className="mt-2 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-700">
                  {refreshError}
                </div>
              ) : refreshNotice ? (
                <div
                  className={`mt-2 rounded-xl border px-3 py-2 text-[11px] leading-5 ${
                    refreshNotice.tone === "amber"
                      ? "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-blue-200 bg-blue-50 text-blue-800"
                  }`}
                >
                  <div className="font-bold">{refreshNotice.title}</div>
                  <div className="mt-0.5 opacity-90">{refreshNotice.description}</div>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() =>
                setDailyDisplayMode((current) => (current === "amount" ? "percent" : "amount"))
              }
              className="text-right transition hover:opacity-80"
              title="点击切换：当日收益额 / 当日收益率"
            >
              <div className="text-[11px] font-semibold text-slate-400">
                {dailyDisplayMode === "amount" ? "当日收益" : "当日收益率"}
                {quoteTradeDate ? ` ${quoteTradeDate}` : ""}
              </div>
              <div
                className={`mt-0.5 text-lg font-black tabular-nums ${cnProfitClass(
                  dailyDisplayMode === "amount" ? dailyProfit : dailyReturn,
                )}`}
              >
                {dailyDisplayMode === "amount"
                  ? formatSignedMoney(dailyProfit)
                  : dailyReturn != null
                    ? formatSignedPercent(dailyReturn)
                    : "—"}
              </div>
            </button>
          </div>
        </div>

        <div className="grid grid-cols-[minmax(0,1fr)_5rem_5rem_5rem] gap-1.5 border-b border-slate-100 bg-slate-50/80 px-3 py-2.5 text-[11px] font-bold text-slate-500 sm:gap-2 sm:px-4">
          <div className="flex items-center gap-1.5">
            <Settings2 size={12} className="text-slate-400" />
            <span>基金 / 持有金额</span>
          </div>
          <SortableColumnHeader
            label="估算当日"
            date={quoteTradeDate}
            columnKey="daily"
            activeSortKey={sortKey}
            sortDir={sortDir}
            onSort={() => handleSort("daily")}
          />
          <SortableColumnHeader
            label="关联板块"
            date={quoteTradeDate}
            columnKey="sector"
            activeSortKey={sortKey}
            sortDir={sortDir}
            onSort={() => handleSort("sector")}
          />
          <SortableColumnHeader
            label="持有收益"
            date={quoteTradeDate}
            columnKey="holding"
            activeSortKey={sortKey}
            sortDir={sortDir}
            onSort={() => handleSort("holding")}
          />
        </div>

        <ul className="divide-y divide-slate-100">
          {sortedHoldings.map(({ holding, index }) => {
            const daily = computeDailyProfit(holding);
            const yesterday = computeYesterdayProfit(holding);
            const estimatedDailyReturn = computeEstimatedDailyReturnPercent(holding);
            const holdingProfit = computeHoldingProfit(holding);
            const holdingReturn = computeEstimatedHoldingReturnPercent(holding);
            const dailyIsEstimated = dailyProfitIsEstimated(holding);
            const sectorReturn = resolveSectorBoardReturnPercent(holding);
            const quoteLabel = sectorQuoteLookupLabel(holding);

            return (
              <li key={`${holding.fund_code}-${index}`}>
                <button
                  type="button"
                  onClick={() => onSelectHolding?.(index)}
                  className="grid w-full grid-cols-[minmax(0,1fr)_5rem_5rem_5rem] gap-1.5 px-3 py-3.5 text-left transition hover:bg-slate-50 active:bg-slate-100 sm:gap-2 sm:px-4"
                >
                  <div className="min-w-0">
                    <div className="truncate text-[15px] font-bold leading-snug text-slate-900">
                      {holding.fund_name}
                    </div>
                    {!amountsHidden ? (
                      <div className="mt-1 text-xs text-slate-500 tabular-nums">
                        ¥ {formatMoney(holding.holding_amount)}
                      </div>
                    ) : null}
                  </div>

                  <div
                    className="text-right"
                    title={
                      holding.daily_return_percent_source === "official_nav"
                        ? "官方净值已公布，使用真实当日涨跌"
                        : "由关联板块或场内指数涨跌估算；取不到真实板块时可能为天天基金估值兜底"
                    }
                  >
                    <div className={`text-[15px] font-black tabular-nums ${cnProfitClass(daily)}`}>
                      {daily != null ? formatSignedMoney(daily) : "—"}
                    </div>
                    {estimatedDailyReturn != null ? (
                      <div
                        className={`mt-0.5 text-[11px] font-bold tabular-nums ${cnProfitClass(estimatedDailyReturn)}`}
                      >
                        {dailyIsEstimated ? "≈" : ""}
                        {formatSignedPercent(estimatedDailyReturn)}
                      </div>
                    ) : (
                      <div className="mt-0.5 text-[11px] text-slate-300">—</div>
                    )}
                    {yesterday != null ? (
                      <div
                        className={`mt-0.5 text-[10px] font-semibold tabular-nums text-slate-400 ${cnProfitClass(yesterday)}`}
                        title="昨日收益：再上一交易日的官方净值涨跌"
                      >
                        昨 {formatSignedMoney(yesterday)}
                      </div>
                    ) : null}
                  </div>

                  <div className="text-right">
                    <div className={`text-[15px] font-black tabular-nums ${cnProfitClass(sectorReturn)}`}>
                      {formatSignedPercent(sectorReturn)}
                    </div>
                    <div className="mt-0.5 truncate text-[10px] text-slate-400" title={quoteLabel ?? undefined}>
                      {quoteLabel ?? "—"}
                    </div>
                  </div>

                  <div className="text-right">
                    <div className={`text-[15px] font-black tabular-nums ${cnProfitClass(holdingProfit)}`}>
                      {formatSignedMoney(holdingProfit)}
                    </div>
                    {holdingReturn != null ? (
                      <div className={`mt-0.5 text-[11px] font-bold tabular-nums ${cnProfitClass(holdingReturn)}`}>
                        {holdingProfitIsEstimated(holding) ? "≈" : ""}
                        {formatSignedPercent(holdingReturn)}
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
