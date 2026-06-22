"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowLeftRight,
  ArrowUp,
  Eye,
  EyeOff,
  Plus,
  RefreshCw,
  ScanLine,
} from "lucide-react";
import { fetchTradingSession, type Holding, type PortfolioSummary } from "@/lib/api";
import { SectorMappingModal } from "@/components/SectorMappingModal";
import {
  cnProfitClass,
  formatSignedMoney,
  formatSignedPercent,
  resolveSectorBoardReturnPercent,
  sumDailyProfit,
  sumHoldingAmount,
  displayableHoldings,
  type HoldingIdentity,
} from "@/lib/holdingMetrics";
import {
  getDailyProfit,
  getEstimatedDailyReturnPercent,
  getEstimatedHoldingProfit,
  getEstimatedHoldingReturnPercent,
  isDailyProfitEstimated,
  isHoldingReturnEstimated,
} from "@/lib/holdingDisplay";
import type { SectorQuoteMeta } from "@/lib/api";
import { holdingDisplaySectorLabel } from "@/lib/profileSector";
import { buildSectorRefreshNotice } from "@/lib/sectorQuoteStatus";
import { formatThemeBoardUpdatedFromIso } from "@/lib/marketThemeBoard";
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
  refreshedAt?: string | null;
  isLoading?: boolean;
  className?: string;
  onAddHolding?: () => void;
  onBatchTransaction?: () => void;
  onSelectHolding?: (holding: HoldingIdentity) => void;
};

const updatedBadgeClassName =
  "shrink-0 rounded border border-blue-200 bg-blue-50 px-1 py-0.5 text-[10px] font-bold text-blue-700";

function UpdatedBadge({ className = "" }: { className?: string }) {
  return <span className={`${updatedBadgeClassName} ${className}`.trim()}>已更新</span>;
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
      return getDailyProfit(holding);
    case "sector":
      return resolveSectorBoardReturnPercent(holding);
    case "holding":
      return getEstimatedHoldingProfit(holding);
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
  date?: string | null;
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

function formatHoldingsRefreshedLabel(
  iso: string | null | undefined,
  isRefreshing: boolean,
): string {
  if (iso) {
    const formatted = formatThemeBoardUpdatedFromIso(iso);
    if (formatted !== "加载中…") {
      return formatted;
    }
  }
  if (isRefreshing) {
    return "刷新中…";
  }
  return "尚未刷新板块行情";
}

export function YangjibaoHoldingsBoard({
  holdings,
  portfolioSummary,
  sectorRefresh,
  refreshedAt = null,
  isLoading = false,
  className,
  onAddHolding,
  onBatchTransaction,
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
    sectorMetaByFundCode,
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

  const displayHoldings = useMemo(() => displayableHoldings(holdings), [holdings]);
  const refreshNotice = buildSectorRefreshNotice(lastRefreshResult);

  const computedTotal = sumHoldingAmount(displayHoldings);
  const computedDaily = sumDailyProfit(displayHoldings);
  const totalAssets = computedTotal || portfolioSummary?.total_assets || null;
  const dailyProfit = displayHoldings.length > 0 ? computedDaily : null;
  const dailyReturn = accountDailyReturnPercent(dailyProfit, totalAssets);
  const officialDailyCount = displayHoldings.filter(
    (holding) => holding.daily_return_percent_source === "official_nav",
  ).length;
  const allOfficialDaily =
    displayHoldings.length > 0 && officialDailyCount === displayHoldings.length;
  const dailyColumnLabel = allOfficialDaily ? "当日" : "估算";

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
      [...displayHoldings].sort((left, right) =>
        compareHoldingsBySort(left, right, sortKey, sortDir),
      ),
    [displayHoldings, sortDir, sortKey],
  );

  const sectionClassName = className ?? "max-w-none";

  if (!displayHoldings.length) {
    return (
      <section className={`mx-auto w-full ${sectionClassName}`}>
        <div className="section-card overflow-hidden">
          {isLoading ? (
            <div className="px-5 py-12 text-center">
              <p className="text-sm font-bold text-slate-500">账户汇总</p>
              <p className="mt-6 text-3xl font-black text-slate-300">—</p>
              <p className="mt-6 text-sm leading-6 text-slate-400">
                正在恢复上次持仓，并尝试刷新真实板块涨跌…
              </p>
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-state-icon">
                <ScanLine size={26} strokeWidth={2.2} />
              </span>
              <h3 className="text-lg font-black text-slate-900">截张图，30 秒看懂你的基金</h3>
              <p className="max-w-xs text-sm leading-6 text-slate-500">
                上传支付宝或养基宝的持仓截图，好基灵自动识别基金、份额与收益，并实时关联板块涨跌。
              </p>
              {onAddHolding ? (
                <div className="mt-2 flex flex-wrap items-center justify-center gap-2.5">
                  <button type="button" onClick={onAddHolding} className="btn-primary !px-5 !py-2.5 !text-sm">
                    <Plus size={16} />
                    上传截图 / 新增持有
                  </button>
                  {onBatchTransaction ? (
                    <button
                      type="button"
                      onClick={onBatchTransaction}
                      className="btn-secondary !px-5 !py-2.5 !text-sm"
                    >
                      <ArrowLeftRight size={16} />
                      批量加减仓
                    </button>
                  ) : null}
                </div>
              ) : null}
              <p className="mt-1 text-xs text-slate-400">数据仅本地识别，不上传原始截图</p>
            </div>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className={`mx-auto w-full ${sectionClassName}`}>
      <div className="section-card overflow-hidden">
        <div className="holdings-hero border-b border-slate-100 px-4 pb-3.5 pt-4">
          <div className="mb-3 flex items-center justify-end gap-1">
            <button
              type="button"
              onClick={() => {
                setAmountsHidden((current) => {
                  const next = !current;
                  saveAmountsHidden(next);
                  return next;
                });
              }}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100"
              title={amountsHidden ? "显示金额" : "隐藏金额"}
              aria-label={amountsHidden ? "显示金额" : "隐藏金额"}
            >
              {amountsHidden ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
            <button
              type="button"
              onClick={() => void refresh(true, "accurate")}
              disabled={isRefreshing}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-50"
              title="刷新板块涨跌"
            >
              <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
            </button>
          </div>

          <div className="flex items-end justify-between gap-4">
            <div className="min-w-0">
              <div className="text-[11px] font-semibold text-slate-400">总资产</div>
              <p className="mt-0.5 text-[10px] font-medium text-slate-400">
                {formatHoldingsRefreshedLabel(refreshedAt, isRefreshing)}
              </p>
              <div className="kpi-value mt-1 text-[2.15rem] leading-none text-slate-950">
                {formatBalance(totalAssets, amountsHidden)}
              </div>
              {refreshError ? (
                <div className="mt-2 text-xs text-rose-600">{refreshError}</div>
              ) : refreshNotice?.tone === "amber" ? (
                <div className="mt-2 text-xs leading-5 text-amber-700">{refreshNotice.description}</div>
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
                {allOfficialDaily ? <UpdatedBadge className="ml-1.5 inline-flex px-1.5" /> : null}
              </div>
              <div
                className={`font-display mt-0.5 text-xl font-extrabold tabular-nums ${cnProfitClass(
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

        {quoteTradeDate ? (
          <div className="border-b border-slate-100 bg-slate-50/60 px-3 py-1 text-[10px] font-semibold text-slate-400 sm:px-4">
            行情日 {quoteTradeDate}
          </div>
        ) : null}
        <div className="grid grid-cols-[minmax(0,1fr)_4.25rem_minmax(3.5rem,5rem)_4.25rem] gap-1 border-b border-slate-100 bg-slate-50/80 px-3 py-1.5 text-[10px] font-bold text-slate-500 sm:px-4">
          <span>基金</span>
          <SortableColumnHeader
            label={dailyColumnLabel}
            date={null}
            columnKey="daily"
            activeSortKey={sortKey}
            sortDir={sortDir}
            onSort={() => handleSort("daily")}
          />
          <SortableColumnHeader
            label="板块"
            date={null}
            columnKey="sector"
            activeSortKey={sortKey}
            sortDir={sortDir}
            onSort={() => handleSort("sector")}
          />
          <SortableColumnHeader
            label="持有"
            date={null}
            columnKey="holding"
            activeSortKey={sortKey}
            sortDir={sortDir}
            onSort={() => handleSort("holding")}
          />
        </div>

        <ul className="divide-y divide-slate-100">
          {sortedHoldings.map((holding) => {
            const daily = getDailyProfit(holding);
            const estimatedDailyReturn = getEstimatedDailyReturnPercent(holding);
            const holdingProfit = getEstimatedHoldingProfit(holding);
            const holdingReturn = getEstimatedHoldingReturnPercent(holding);
            const dailyIsEstimated = isDailyProfitEstimated(holding);
            const isOfficialDaily = holding.daily_return_percent_source === "official_nav";
            const sectorReturn = resolveSectorBoardReturnPercent(holding);
            const sectorMeta = sectorMetaByFundCode[holding.fund_code] as SectorQuoteMeta | undefined;
            const sectorLabel = holdingDisplaySectorLabel(holding, sectorMeta);
            return (
              <li key={`${holding.fund_code}-${holding.fund_name}`}>
                <button
                  type="button"
                  onClick={() =>
                    onSelectHolding?.({
                      fund_code: holding.fund_code,
                      fund_name: holding.fund_name,
                    })
                  }
                  className="grid w-full grid-cols-[minmax(0,1fr)_4.25rem_minmax(3.5rem,5rem)_4.25rem] gap-1 px-3 py-2 text-left transition hover:bg-slate-50 active:bg-slate-100 sm:px-4"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1">
                      <div className="truncate text-[13px] font-bold leading-tight text-slate-900">
                        {holding.fund_name}
                      </div>
                      {isOfficialDaily ? <UpdatedBadge className="!px-0.5 !py-0 !text-[9px]" /> : null}
                    </div>
                    {!amountsHidden ? (
                      <div className="mt-0.5 text-[10px] text-slate-400 tabular-nums">
                        {formatMoney(holding.holding_amount)}
                      </div>
                    ) : null}
                  </div>

                  <div
                    className="text-right leading-tight"
                    title={
                      holding.daily_return_percent_source === "official_nav"
                        ? "官方净值已公布"
                        : "板块或指数涨跌估算"
                    }
                  >
                    <div className={`text-[13px] font-black tabular-nums ${cnProfitClass(daily)}`}>
                      {daily != null ? formatSignedMoney(daily) : "—"}
                    </div>
                    {estimatedDailyReturn != null ? (
                      <div className={`text-[10px] font-semibold tabular-nums ${cnProfitClass(estimatedDailyReturn)}`}>
                        {!isOfficialDaily && dailyIsEstimated ? "≈" : ""}
                        {formatSignedPercent(estimatedDailyReturn)}
                      </div>
                    ) : null}
                  </div>

                  <div
                    className="min-w-0 text-right leading-tight"
                    title={sectorLabel !== "—" ? sectorLabel : undefined}
                  >
                    <div className={`text-[13px] font-black tabular-nums ${cnProfitClass(sectorReturn)}`}>
                      {formatSignedPercent(sectorReturn)}
                    </div>
                    {sectorLabel !== "—" ? (
                      <div className="truncate text-[10px] font-semibold text-slate-500">{sectorLabel}</div>
                    ) : null}
                  </div>

                  <div className="text-right leading-tight">
                    <div className={`text-[13px] font-black tabular-nums ${cnProfitClass(holdingProfit)}`}>
                      {formatSignedMoney(holdingProfit)}
                    </div>
                    {holdingReturn != null ? (
                      <div className={`text-[10px] font-semibold tabular-nums ${cnProfitClass(holdingReturn)}`}>
                        {isHoldingReturnEstimated(holding) ? "≈" : ""}
                        {formatSignedPercent(holdingReturn)}
                      </div>
                    ) : null}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>

        {onAddHolding ? (
          <div className="flex border-t border-slate-100">
            <button
              type="button"
              onClick={onAddHolding}
              className="flex flex-1 items-center justify-center gap-1.5 bg-white py-2.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50"
            >
              <Plus size={16} />
              新增持有
            </button>
            {onBatchTransaction ? (
              <button
                type="button"
                onClick={onBatchTransaction}
                className="flex flex-1 items-center justify-center gap-1.5 border-l border-slate-100 bg-white py-2.5 text-sm font-bold text-[var(--brand)] transition hover:bg-[var(--brand-soft)]"
              >
                <ArrowLeftRight size={16} />
                批量加减仓
              </button>
            ) : null}
          </div>
        ) : null}
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

