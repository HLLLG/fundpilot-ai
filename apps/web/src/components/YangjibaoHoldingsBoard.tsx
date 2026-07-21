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
import { type Holding, type PortfolioSummary } from "@/lib/api";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";
import { hydrateTradingSession } from "@/lib/tradingSessionClient";
import { readTradingSessionCache } from "@/lib/holdingDetailCache";
import { SectorMappingModal } from "@/components/SectorMappingModal";
import { InlineNotice } from "@/components/InlineNotice";
import {
  cnProfitClass,
  formatSignedMoney,
  formatSignedPercent,
  resolveSectorBoardReturnPercent,
  sumDailyProfit,
  sumPortfolioTotalAssets,
  navigableHoldings,
  holdingIdentityKey,
  type HoldingIdentity,
} from "@/lib/holdingMetrics";
import {
  getDailyProfit,
  getEstimatedDailyReturnPercent,
  getEstimatedHoldingProfit,
  getEstimatedHoldingReturnPercent,
  getSettledHoldingAmount,
  isDailyProfitEstimated,
  isHoldingReturnEstimated,
} from "@/lib/holdingDisplay";
import type { SectorQuoteMeta } from "@/lib/api";
import { holdingDisplaySectorLabel } from "@/lib/profileSector";
import { buildSectorRefreshNotice, isEstimateFallbackMeta } from "@/lib/sectorQuoteStatus";
import { formatThemeBoardUpdatedFromIso } from "@/lib/marketThemeBoard";
import { loadAmountsHidden, saveAmountsHidden } from "@/lib/storage";
import { formatTradeDateShort } from "@/lib/tradeDateLabel";
import type { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";

type SectorRefreshControl = ReturnType<typeof useSectorQuoteRefresh>;
type HoldingsSortKey = "amount" | "daily" | "sector" | "holding";
type HoldingsSortDir = "desc" | "asc";
type DailyDisplayMode = "amount" | "percent";
export type PortfolioLoadState = "loading" | "refreshing" | "ready" | "stale" | "error";

const HOLDINGS_SORT_LABELS: Record<HoldingsSortKey, string> = {
  amount: "持有金额",
  daily: "当日收益",
  sector: "板块涨跌",
  holding: "持有收益",
};

type YangjibaoHoldingsBoardProps = {
  holdings: Holding[];
  portfolioSummary?: PortfolioSummary | null;
  sectorRefresh: SectorRefreshControl;
  refreshedAt?: string | null;
  isLoading?: boolean;
  loadState?: PortfolioLoadState;
  loadError?: string | null;
  onRetryLoad?: () => void;
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
      return getSettledHoldingAmount(holding);
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
    <div className="flex min-h-11 items-center justify-end gap-0.5">
      <div className="text-right">
        <div>{label}</div>
        {date ? <div className="mt-0.5 text-[10px] font-semibold text-slate-500 tabular-nums">{date}</div> : null}
      </div>
      <button
        type="button"
        onClick={onSort}
        className={`inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] ${
          active ? "text-slate-700" : "text-slate-500 hover:text-slate-700"
        }`}
        title={
          active
            ? sortDir === "desc"
              ? "收益从高到低，点击切换"
              : "收益从低到高，点击切换"
            : `按${label}排序`
        }
        aria-label={
          active
            ? `按${label}${sortDir === "desc" ? "降序" : "升序"}排列，点击切换方向`
            : `按${label}降序排列`
        }
        aria-pressed={active}
      >
        {active && sortDir === "asc" ? <ArrowUp size={14} strokeWidth={2.5} /> : <ArrowDown size={14} strokeWidth={2.5} />}
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
  loadState,
  loadError,
  onRetryLoad,
  className,
  onAddHolding,
  onBatchTransaction,
  onSelectHolding,
}: YangjibaoHoldingsBoardProps) {
  const [quoteTradeDate, setQuoteTradeDate] = useState<string | null>(() => {
    const cached = readTradingSessionCache();
    return cached ? formatTradeDateShort(cached.effective_trade_date) : null;
  });
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
    return hydrateTradingSession((session) => {
      setQuoteTradeDate(formatTradeDateShort(session.effective_trade_date));
    });
  }, []);

  const displayHoldings = useMemo(() => navigableHoldings(holdings), [holdings]);
  const refreshNotice = buildSectorRefreshNotice(lastRefreshResult);

  const computedTotal = sumPortfolioTotalAssets(displayHoldings);
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
  const effectiveLoadState = loadState ?? (isLoading ? "loading" : "ready");

  if (!displayHoldings.length) {
    return (
      <section className={`mx-auto w-full ${sectionClassName}`}>
        <div className="holdings-workspace overflow-hidden">
          {effectiveLoadState === "loading" || effectiveLoadState === "refreshing" ? (
            <div className="px-5 py-12 text-center">
              <p className="text-sm font-bold text-slate-500">账户汇总</p>
              <p className="mt-6 text-3xl font-black text-slate-300">—</p>
              <p className="mt-6 text-sm leading-6 text-slate-500">
                正在恢复上次持仓，并尝试刷新真实板块涨跌…
              </p>
            </div>
          ) : effectiveLoadState === "error" || effectiveLoadState === "stale" ? (
            <div className="empty-state" role="alert">
              <span className="empty-state-icon !bg-rose-50 !text-rose-700">
                <RefreshCw size={24} strokeWidth={2.2} />
              </span>
              <h3 className="text-lg font-black text-slate-900">暂时无法确认账户持仓</h3>
              <p className="max-w-sm text-sm leading-6 text-slate-600">
                {loadError ?? "服务暂时不可用。为避免把故障误认成空账户，当前不展示空持仓结论。"}
              </p>
              {onRetryLoad ? (
                <button type="button" onClick={onRetryLoad} className="btn-primary !min-h-11 !px-5 !text-sm">
                  重新加载
                </button>
              ) : null}
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-state-icon">
                <ScanLine size={26} strokeWidth={2.2} />
              </span>
              <h3 className="text-lg font-black text-slate-900">录入第一笔持仓</h3>
              <p className="max-w-xs text-sm leading-6 text-slate-500">
                上传支付宝或养基宝截图，或直接手动添加。确认后保存到你的账户。
              </p>
              {onAddHolding ? (
                <div className="mt-2 flex flex-wrap items-center justify-center gap-2.5">
                  <button type="button" onClick={onAddHolding} className="btn-primary !min-h-11 !px-5 !py-2.5 !text-sm">
                    <Plus size={16} />
                    上传截图 / 新增持有
                  </button>
                  {onBatchTransaction ? (
                    <button
                      type="button"
                      onClick={onBatchTransaction}
                      className="btn-secondary !min-h-11 !px-5 !py-2.5 !text-sm"
                    >
                      <ArrowLeftRight size={16} />
                      批量加减仓
                    </button>
                  ) : null}
                </div>
              ) : null}
              <p className="mt-1 max-w-md text-xs leading-5 text-slate-500">
                {OCR_PRIVACY_COPY.uploadNotice}
              </p>
            </div>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className={`mx-auto w-full ${sectionClassName}`}>
      <div className="holdings-workspace overflow-hidden">
        {effectiveLoadState === "refreshing" ? (
          <InlineNotice
            tone="info"
            message="正在同步最新持仓，当前先显示上次缓存。"
            className="m-3"
          />
        ) : effectiveLoadState === "stale" ? (
          <InlineNotice
            tone="warning"
            message={loadError ?? "最新持仓暂时加载失败，当前显示的是上次缓存。"}
            action={onRetryLoad ? { label: "重试", onClick: onRetryLoad } : undefined}
            className="m-3"
          />
        ) : null}
        <div className="holdings-hero holdings-summary border-b border-[var(--line-strong)] px-4 pb-5 pt-4 sm:px-6 sm:pt-5">
          <div className="mb-2 flex items-center justify-end gap-1">
            <button
              type="button"
              onClick={() => {
                setAmountsHidden((current) => {
                  const next = !current;
                  saveAmountsHidden(next);
                  return next;
                });
              }}
              className="inline-flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]"
              title={amountsHidden ? "显示金额" : "隐藏金额"}
              aria-label={amountsHidden ? "显示金额" : "隐藏金额"}
            >
              {amountsHidden ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
            <button
              type="button"
              onClick={() => void refresh(true, "accurate")}
              disabled={isRefreshing}
              className="inline-flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] disabled:opacity-50"
              title="刷新板块涨跌"
              aria-label={isRefreshing ? "正在刷新板块涨跌" : "刷新板块涨跌"}
            >
              <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
            </button>
          </div>

          <div className="grid gap-4 min-[380px]:grid-cols-[minmax(0,1fr)_auto] min-[380px]:items-end">
            <div className="min-w-0">
              <div className="text-[11px] font-semibold text-slate-500">总资产</div>
              <p className="mt-0.5 text-[10px] font-medium text-slate-500">
                {formatHoldingsRefreshedLabel(refreshedAt, isRefreshing)}
              </p>
              <div className="kpi-value mt-1 break-all text-[clamp(1.85rem,10vw,2.15rem)] leading-none text-slate-950">
                {formatBalance(totalAssets, amountsHidden)}
              </div>
              {refreshError ? (
                <div role="alert" className="mt-2 text-xs text-rose-700">
                  {refreshError}
                </div>
              ) : refreshNotice?.tone === "amber" ? (
                <div className="mt-2 text-xs leading-5 text-amber-700">
                  部分基金无真实关联板块，已用「
                  <span className="font-bold">估值</span>
                  」标签的基金代替展示天天基金净值估值
                </div>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() =>
                setDailyDisplayMode((current) => (current === "amount" ? "percent" : "amount"))
              }
              className="min-h-11 min-w-0 justify-self-start text-left transition hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] min-[380px]:justify-self-end min-[380px]:text-right"
              title="点击切换：当日收益额 / 当日收益率"
              aria-label={`当前显示${dailyDisplayMode === "amount" ? "当日收益额" : "当日收益率"}，点击切换`}
            >
              <div className="text-[11px] font-semibold text-slate-500">
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
          <div className="border-b border-slate-100 bg-slate-50/60 px-3 py-1 text-[10px] font-semibold text-slate-500 sm:px-4">
            行情日 {quoteTradeDate}
          </div>
        ) : null}

        <div
          className="flex items-center gap-2 border-b border-slate-100 bg-slate-50/80 px-3 py-2 sm:hidden"
          data-testid="mobile-holdings-sort"
        >
          <label className="flex min-w-0 flex-1 items-center gap-2">
            <span className="shrink-0 text-xs font-bold text-slate-500">排序</span>
            <select
              value={sortKey}
              onChange={(event) => {
                setSortKey(event.target.value as HoldingsSortKey);
                setSortDir("desc");
              }}
              className="min-h-11 min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 outline-none focus:border-[var(--brand)] focus:ring-2 focus:ring-blue-100"
              aria-label="持仓排序方式"
            >
              {(Object.keys(HOLDINGS_SORT_LABELS) as HoldingsSortKey[]).map((key) => (
                <option key={key} value={key}>
                  {HOLDINGS_SORT_LABELS[key]}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => setSortDir((current) => (current === "desc" ? "asc" : "desc"))}
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-blue-200 hover:text-[var(--brand)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]"
            aria-label={`当前${sortDir === "desc" ? "降序" : "升序"}，点击切换为${sortDir === "desc" ? "升序" : "降序"}`}
          >
            {sortDir === "desc" ? <ArrowDown size={16} /> : <ArrowUp size={16} />}
          </button>
        </div>

        <div
          className="hidden grid-cols-[minmax(0,1fr)_4.25rem_minmax(3.5rem,5rem)_4.25rem] items-center gap-1 border-b border-slate-100 bg-slate-50/80 px-4 text-[10px] font-bold text-slate-500 sm:grid"
          data-testid="desktop-holdings-header"
        >
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

        <ul className="holdings-ledger space-y-2 p-2 sm:space-y-0 sm:divide-y sm:divide-[var(--line)] sm:p-0">
          {sortedHoldings.map((holding, rowIndex) => {
            const daily = getDailyProfit(holding);
            const estimatedDailyReturn = getEstimatedDailyReturnPercent(holding);
            const holdingProfit = getEstimatedHoldingProfit(holding);
            const holdingReturn = getEstimatedHoldingReturnPercent(holding);
            const dailyIsEstimated = isDailyProfitEstimated(holding);
            const profitAccrualDeferred = holding.profit_accrual_deferred === true;
            const isOfficialDaily = holding.daily_return_percent_source === "official_nav";
            const sectorReturn = resolveSectorBoardReturnPercent(holding);
            const sectorMeta = sectorMetaByFundCode[holding.fund_code] as SectorQuoteMeta | undefined;
            const sectorLabel = holdingDisplaySectorLabel(holding, sectorMeta);
            const holdingAmountLabel = amountsHidden
              ? "持有金额已隐藏"
              : `持有金额 ${formatMoney(getSettledHoldingAmount(holding))}`;
            const rowAriaLabel = [
              holding.fund_name,
              holdingAmountLabel,
              `${dailyColumnLabel}收益 ${daily != null ? formatSignedMoney(daily) : "暂无"}`,
              estimatedDailyReturn != null ? formatSignedPercent(estimatedDailyReturn) : null,
              `板块涨跌 ${formatSignedPercent(sectorReturn)}`,
              sectorLabel !== "—" ? sectorLabel : null,
              `持有收益 ${formatSignedMoney(holdingProfit)}`,
              holdingReturn != null ? formatSignedPercent(holdingReturn) : null,
            ]
              .filter(Boolean)
              .join("，");
            return (
              <li key={`${holdingIdentityKey(holding)}-${rowIndex}`}>
                <button
                  type="button"
                  data-testid="holding-row"
                  onClick={() =>
                    onSelectHolding?.({
                      fund_code: holding.fund_code,
                      fund_name: holding.fund_name,
                    })
                  }
                  aria-label={rowAriaLabel}
                  className="holding-ledger-row grid min-h-11 w-full grid-cols-3 gap-x-2 gap-y-3 rounded-[var(--radius-control)] border border-[var(--line)] bg-[var(--panel)] px-3 py-3 text-left transition hover:border-[var(--line-strong)] hover:bg-[var(--brand-soft)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] active:bg-[var(--surface-muted)] sm:grid-cols-[minmax(0,1fr)_4.25rem_minmax(3.5rem,5rem)_4.25rem] sm:gap-1 sm:rounded-none sm:border-0 sm:bg-transparent sm:px-4 sm:py-2"
                >
                  <div className="col-span-3 min-w-0 sm:col-span-1">
                    <div className="flex items-center gap-1">
                      <div className="line-clamp-2 break-words text-sm font-bold leading-5 text-slate-900 sm:truncate sm:text-[13px] sm:leading-tight">
                        {holding.fund_name}
                      </div>
                      {isOfficialDaily ? <UpdatedBadge className="!px-0.5 !py-0 !text-[9px]" /> : null}
                    </div>
                    {!amountsHidden ? (
                      <div className="mt-0.5 text-[10px] text-slate-500 tabular-nums">
                        {formatMoney(getSettledHoldingAmount(holding))}
                      </div>
                    ) : null}
                  </div>

                  <div
                    className="min-w-0 text-left leading-tight sm:text-right"
                    title={
                      profitAccrualDeferred
                        ? "份额待确认，次交易日起计收益（与支付宝一致）"
                        : holding.daily_return_percent_source === "official_nav"
                        ? "官方净值已公布"
                        : "板块或指数涨跌估算"
                    }
                  >
                    <span className="mb-1 block text-[10px] font-bold text-slate-500 sm:hidden">
                      {dailyColumnLabel}收益
                    </span>
                    <div className={`whitespace-nowrap text-xs font-black tracking-tight tabular-nums sm:text-[13px] sm:tracking-normal ${cnProfitClass(daily)}`}>
                      {daily != null ? formatSignedMoney(daily) : "—"}
                    </div>
                    {estimatedDailyReturn != null ? (
                      <div className={`mt-1 whitespace-nowrap text-[11px] font-semibold tracking-tight tabular-nums sm:mt-0 sm:text-[10px] sm:tracking-normal ${cnProfitClass(estimatedDailyReturn)}`}>
                        {!isOfficialDaily && dailyIsEstimated ? "≈" : ""}
                        {formatSignedPercent(estimatedDailyReturn)}
                      </div>
                    ) : null}
                  </div>

                  <div
                    className="min-w-0 border-l border-slate-100 pl-2 text-left leading-tight sm:border-0 sm:pl-0 sm:text-right"
                    title={
                      sectorLabel !== "—"
                        ? isEstimateFallbackMeta(sectorMeta)
                          ? `${sectorLabel}（无真实关联板块行情，当前用天天基金净值估值代替）`
                          : sectorLabel
                        : undefined
                    }
                  >
                    <span className="mb-1 block text-[10px] font-bold text-slate-500 sm:hidden">板块涨跌</span>
                    <div className={`whitespace-nowrap text-xs font-black tracking-tight tabular-nums sm:text-[13px] sm:tracking-normal ${cnProfitClass(sectorReturn)}`}>
                      {formatSignedPercent(sectorReturn)}
                    </div>
                    {sectorLabel !== "—" ? (
                      <div className="mt-1 flex min-w-0 items-center justify-start gap-1 sm:mt-0 sm:justify-end">
                        {isEstimateFallbackMeta(sectorMeta) ? (
                          <span className="shrink-0 rounded border border-amber-200 bg-amber-50 px-1 py-0 text-[8px] font-bold leading-4 text-amber-600">
                            估值
                          </span>
                        ) : null}
                        <span className="truncate text-[10px] font-semibold text-slate-500">{sectorLabel}</span>
                      </div>
                    ) : null}
                  </div>

                  <div className="min-w-0 border-l border-slate-100 pl-2 text-left leading-tight sm:border-0 sm:pl-0 sm:text-right">
                    <span className="mb-1 block text-[10px] font-bold text-slate-500 sm:hidden">持有收益</span>
                    <div className={`whitespace-nowrap text-xs font-black tracking-tight tabular-nums sm:text-[13px] sm:tracking-normal ${cnProfitClass(holdingProfit)}`}>
                      {formatSignedMoney(holdingProfit)}
                    </div>
                    {holdingReturn != null ? (
                      <div className={`mt-1 whitespace-nowrap text-[11px] font-semibold tracking-tight tabular-nums sm:mt-0 sm:text-[10px] sm:tracking-normal ${cnProfitClass(holdingReturn)}`}>
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
              className="flex min-h-11 flex-1 items-center justify-center gap-1.5 bg-white px-2 py-2.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--brand)]"
            >
              <Plus size={16} />
              新增持有
            </button>
            {onBatchTransaction ? (
              <button
                type="button"
                onClick={onBatchTransaction}
                className="flex min-h-11 flex-1 items-center justify-center gap-1.5 border-l border-slate-100 bg-white px-2 py-2.5 text-sm font-bold text-[var(--brand)] transition hover:bg-[var(--brand-soft)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--brand)]"
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

