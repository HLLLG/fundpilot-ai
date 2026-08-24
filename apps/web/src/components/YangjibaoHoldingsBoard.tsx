"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowLeftRight,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Plus,
  Receipt,
  RefreshCw,
  ScanLine,
} from "lucide-react";
import { type DeletePortfolioTransactionResult, type Holding, type PortfolioSummary } from "@/lib/api";
import { hydrateTradingSession } from "@/lib/tradingSessionClient";
import { readTradingSessionCache } from "@/lib/holdingDetailCache";
import { SectorMappingModal } from "@/components/SectorMappingModal";
import { HoldingsTransactionLedgerModal } from "@/components/HoldingsTransactionLedgerModal";
import { InlineNotice } from "@/components/InlineNotice";
import { MethodologyNote } from "@/components/MethodologyNote";
import {
  cnProfitClass,
  computeHoldingWeight,
  formatHoldingDays,
  formatHoldingUnitCost,
  formatPlainMoney,
  formatPlainPercent,
  formatSignedMoney,
  formatSignedPercent,
  getHoldingDays,
  getHoldingShares,
  getHoldingUnitCost,
  resolveSectorBoardReturnPercent,
  sumDailyProfit,
  portfolioOfficialNavSettled,
  sumPortfolioTotalAssets,
  navigableHoldings,
  holdingForCurrentSession,
  holdingHasCurrentOfficialNav,
  holdingIdentityKey,
  isUnsettledPreviewHolding,
  pendingBuyAmount,
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
import { loadAmountsHidden, saveAmountsHidden } from "@/lib/storage";
import { formatHoldingsColumnDateShort, formatTradeDateShort } from "@/lib/tradeDateLabel";
import type { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";

type SectorRefreshControl = ReturnType<typeof useSectorQuoteRefresh>;
type HoldingsSortKey =
  | "amount"
  | "daily"
  | "sector"
  | "holding"
  | "weight"
  | "shares"
  | "cost"
  | "days";
type HoldingsSortDir = "desc" | "asc";
export type PortfolioLoadState = "loading" | "refreshing" | "ready" | "stale" | "error";
type HoldingsMetricKey = Exclude<HoldingsSortKey, "amount">;

const HOLDINGS_METRIC_COLUMNS: Array<{
  key: HoldingsMetricKey;
  label: string;
}> = [
  { key: "daily", label: "当日收益" },
  { key: "sector", label: "关联板块" },
  { key: "holding", label: "持有收益" },
  { key: "weight", label: "持仓占比" },
  { key: "shares", label: "持有份额" },
  { key: "cost", label: "持有成本" },
  { key: "days", label: "持有天数" },
];

type YangjibaoHoldingsBoardProps = {
  holdings: Holding[];
  portfolioSummary?: PortfolioSummary | null;
  sectorRefresh: SectorRefreshControl;
  isLoading?: boolean;
  loadState?: PortfolioLoadState;
  loadError?: string | null;
  onRetryLoad?: () => void;
  className?: string;
  onAddHolding?: () => void;
  onBatchTransaction?: () => void;
  onSelectHolding?: (holding: HoldingIdentity) => void;
  onOpenAnalysis?: () => void;
  onDeleteTransaction?: (transactionId: string) => Promise<DeletePortfolioTransactionResult>;
};

const updatedBadgeClassName =
  "shrink-0 rounded border border-blue-200 bg-blue-50 px-1 py-0.5 text-[10px] font-bold text-blue-700";

function UpdatedBadge({ className = "" }: { className?: string }) {
  return <span className={`${updatedBadgeClassName} ${className}`.trim()}>已更新</span>;
}

// formatter 提到模块作用域：持仓看板每行都会调用它。输出格式不变。
const MONEY_FORMATTER = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return MONEY_FORMATTER.format(value);
}

function formatYuan(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `¥${MONEY_FORMATTER.format(value)}`;
}

function formatBalance(value: number | null | undefined, hidden: boolean) {
  if (hidden) {
    return "****";
  }
  return formatMoney(value);
}

const SUMMARY_ICON_BTN =
  "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-[var(--muted)] transition hover:bg-[var(--surface-muted)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]";

function holdingsSortValue(
  holding: Holding,
  key: HoldingsSortKey,
  totalAssets: number | null,
): number | null {
  switch (key) {
    case "daily":
      return getDailyProfit(holding);
    case "sector":
      return resolveSectorBoardReturnPercent(holding);
    case "holding":
      return getEstimatedHoldingProfit(holding);
    case "weight":
      return computeHoldingWeight(holding, totalAssets);
    case "shares":
      return getHoldingShares(holding);
    case "cost":
      return getHoldingUnitCost(holding);
    case "days":
      return getHoldingDays(holding);
    case "amount":
      return getSettledHoldingAmount(holding) || pendingBuyAmount(holding) || null;
  }
}

function compareHoldingsBySort(
  left: Holding,
  right: Holding,
  key: HoldingsSortKey,
  dir: HoldingsSortDir,
  totalAssets: number | null,
): number {
  const leftValue = holdingsSortValue(left, key, totalAssets);
  const rightValue = holdingsSortValue(right, key, totalAssets);
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
  columnKey: HoldingsMetricKey;
  activeSortKey: HoldingsSortKey;
  sortDir: HoldingsSortDir;
  onSort: () => void;
}) {
  const active = activeSortKey === columnKey;

  return (
    <button
      type="button"
      onClick={onSort}
      className={`inline-flex min-h-11 w-full flex-col items-center justify-center gap-0 rounded-lg px-0.5 py-1 text-center leading-tight transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] ${
        active ? "text-slate-700" : "text-slate-500 hover:text-slate-700"
      }`}
      title={
        active
          ? sortDir === "desc"
            ? `${label}从高到低，点击切换`
            : `${label}从低到高，点击切换`
          : `按${label}排序`
      }
      aria-label={
        active
          ? `按${label}${sortDir === "desc" ? "降序" : "升序"}排列，点击切换方向`
          : `按${label}降序排列`
      }
      aria-pressed={active}
    >
      <span>{label}</span>
      <span className="inline-flex items-center gap-0.5 font-semibold tabular-nums">
        {date ? <span>{date}</span> : null}
        {active && sortDir === "asc" ? (
          <ArrowUp size={12} strokeWidth={2.5} className="shrink-0" />
        ) : (
          <ArrowDown size={12} strokeWidth={2.5} className="shrink-0" />
        )}
      </span>
    </button>
  );
}

export function YangjibaoHoldingsBoard({
  holdings,
  portfolioSummary,
  sectorRefresh,
  isLoading = false,
  loadState,
  loadError,
  onRetryLoad,
  className,
  onAddHolding,
  onBatchTransaction,
  onSelectHolding,
  onOpenAnalysis,
  onDeleteTransaction,
}: YangjibaoHoldingsBoardProps) {
  const [quoteTradeDate, setQuoteTradeDate] = useState<string | null>(() => {
    const cached = readTradingSessionCache();
    return cached ? formatTradeDateShort(cached.effective_trade_date) : null;
  });
  const [columnDate, setColumnDate] = useState<string | null>(() => {
    const cached = readTradingSessionCache();
    return cached ? formatHoldingsColumnDateShort(cached) : null;
  });
  const [sessionKind, setSessionKind] = useState<string | null>(() => {
    return readTradingSessionCache()?.session_kind ?? null;
  });
  const [sortKey, setSortKey] = useState<HoldingsSortKey>("amount");
  const [sortDir, setSortDir] = useState<HoldingsSortDir>("desc");
  const [amountsHidden, setAmountsHidden] = useState(() => loadAmountsHidden());
  const [ledgerOpen, setLedgerOpen] = useState(false);
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
      setColumnDate(formatHoldingsColumnDateShort(session));
      setSessionKind(session.session_kind);
    });
  }, []);

  const displayHoldings = useMemo(
    () => navigableHoldings(holdings).map((holding) => holdingForCurrentSession(holding, sessionKind)),
    [holdings, sessionKind],
  );
  const settledHoldings = useMemo(
    () => displayHoldings.filter((holding) => !isUnsettledPreviewHolding(holding)),
    [displayHoldings],
  );
  const pendingTxCount = useMemo(
    () =>
      displayHoldings.reduce(
        (sum, holding) => sum + (holding.pending_transaction_count ?? 0),
        0,
      ),
    [displayHoldings],
  );
  const refreshNotice = buildSectorRefreshNotice(lastRefreshResult);

  const computedTotal = sumPortfolioTotalAssets(settledHoldings);
  const computedDaily = sumDailyProfit(settledHoldings);
  const totalAssets = computedTotal || portfolioSummary?.total_assets || null;
  const dailyProfit = settledHoldings.length > 0 ? computedDaily : null;
  const allOfficialDaily = portfolioOfficialNavSettled(settledHoldings);
  const dailyColumnLabel = allOfficialDaily ? "当日" : "估算";

  const handleSort = (columnKey: HoldingsMetricKey) => {
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
        compareHoldingsBySort(left, right, sortKey, sortDir, totalAssets),
      ),
    [displayHoldings, sortDir, sortKey, totalAssets],
  );

  const tableRef = useRef<HTMLDivElement>(null);
  const metricsHoveringRef = useRef(false);
  const [canScrollMetricsLeft, setCanScrollMetricsLeft] = useState(false);
  const [canScrollMetricsRight, setCanScrollMetricsRight] = useState(false);
  const [metricsScrollbarVisible, setMetricsScrollbarVisible] = useState(false);

  const headerMetricsNode = useCallback(() => {
    return tableRef.current?.querySelector<HTMLElement>("[data-holdings-metrics-scroll]");
  }, []);

  const syncMetricsScrollLeft = useCallback((left: number, source?: HTMLElement | null) => {
    const root = tableRef.current;
    if (!root) {
      return;
    }
    root.querySelectorAll<HTMLElement>("[data-holdings-metrics-scroll]").forEach((node) => {
      if (node !== source && node.scrollLeft !== left) {
        node.scrollLeft = left;
      }
    });
  }, []);

  const updateMetricsScrollHint = useCallback((source?: HTMLElement | null) => {
    const node = source ?? headerMetricsNode();
    if (!node) {
      setCanScrollMetricsLeft(false);
      setCanScrollMetricsRight(false);
      return;
    }
    setCanScrollMetricsLeft(node.scrollLeft > 2);
    setCanScrollMetricsRight(node.scrollWidth - node.clientWidth - node.scrollLeft > 2);
  }, [headerMetricsNode]);

  const revealMetricsScrollbar = useCallback(() => {
    if (metricsHoveringRef.current) {
      setMetricsScrollbarVisible(true);
    }
  }, [setMetricsScrollbarVisible]);

  const scrollMetricsBy = useCallback(
    (delta: number) => {
      const node = headerMetricsNode();
      if (!node) {
        return;
      }
      const next = Math.max(0, Math.min(node.scrollWidth - node.clientWidth, node.scrollLeft + delta));
      node.scrollLeft = next;
      syncMetricsScrollLeft(next, node);
      updateMetricsScrollHint(node);
      revealMetricsScrollbar();
    },
    [headerMetricsNode, revealMetricsScrollbar, syncMetricsScrollLeft, updateMetricsScrollHint],
  );

  const handleMetricsScroll = useCallback(
    (event: React.UIEvent<HTMLElement>) => {
      const source = event.currentTarget;
      syncMetricsScrollLeft(source.scrollLeft, source);
      updateMetricsScrollHint(source);
      revealMetricsScrollbar();
    },
    [revealMetricsScrollbar, syncMetricsScrollLeft, updateMetricsScrollHint],
  );

  useEffect(() => {
    updateMetricsScrollHint();
  }, [sortedHoldings.length, updateMetricsScrollHint]);

  useEffect(() => {
    const root = tableRef.current;
    if (!root) {
      return;
    }
    const onWheel = (event: WheelEvent) => {
      const target = event.target;
      if (!(target instanceof Element) || !target.closest("[data-holdings-metrics-scroll]")) {
        return;
      }
      const node = headerMetricsNode();
      if (!node || node.scrollWidth <= node.clientWidth) {
        return;
      }
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (delta === 0) {
        return;
      }
      event.preventDefault();
      scrollMetricsBy(delta);
    };
    root.addEventListener("wheel", onWheel, { passive: false });
    return () => root.removeEventListener("wheel", onWheel);
  }, [headerMetricsNode, scrollMetricsBy, sortedHoldings.length]);

  const sectionClassName = className ?? "max-w-none";
  const effectiveLoadState = loadState ?? (isLoading ? "loading" : "ready");

  if (!displayHoldings.length) {
    return (
      <section className={`mx-auto w-full ${sectionClassName}`}>
        <div className="holdings-workspace">
          {effectiveLoadState === "loading" || effectiveLoadState === "refreshing" ? (
            <div className="px-5 py-12 text-center">
              <p className="text-sm font-bold text-slate-500">账户汇总</p>
              <p className="mt-6 text-3xl font-black text-slate-300">—</p>
              <p className="mt-6 text-sm text-slate-500">正在加载持仓…</p>
            </div>
          ) : effectiveLoadState === "error" || effectiveLoadState === "stale" ? (
            <div className="empty-state" role="alert">
              <span className="empty-state-icon !bg-rose-50 !text-rose-700">
                <RefreshCw size={24} strokeWidth={2.2} />
              </span>
              <h3 className="text-lg font-black text-slate-900">暂时无法确认账户持仓</h3>
              {/* 只保留可执行信息。原来这里还有一句"为避免把故障误认成空账户，当前不
                  展示空持仓结论"——那是在解释系统的内部设计取舍，用户此刻只需要知道
                  加载失败、可以重试。 */}
              <p className="max-w-sm text-sm leading-6 text-slate-600">
                {loadError ?? "服务暂时不可用，请稍后重试。"}
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
              {/* 两个按钮已经说清了「上传截图」和「手动添加」两条路，描述段不必再复述。 */}
              {onAddHolding ? (
                <div className="mt-1 flex flex-wrap items-center justify-center gap-2.5">
                  <button type="button" onClick={onAddHolding} className="btn-primary !min-h-11 !px-5 !py-2.5 !text-sm">
                    <Plus size={16} />
                    同步持仓
                  </button>
                  {onBatchTransaction ? (
                    <button
                      type="button"
                      onClick={onBatchTransaction}
                      className="btn-secondary !min-h-11 !px-5 !py-2.5 !text-sm"
                    >
                      <ArrowLeftRight size={16} />
                      导入交易
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className={`mx-auto w-full ${sectionClassName}`}>
      <div className="holdings-workspace">
        {effectiveLoadState === "refreshing" ? (
          <InlineNotice
            tone="info"
            message="正在同步最新持仓。"
            className="m-3"
          />
        ) : effectiveLoadState === "stale" ? (
          <InlineNotice
            tone="warning"
            message={loadError ?? "最新持仓暂时加载失败，当前显示的是本次已加载的数据。"}
            action={onRetryLoad ? { label: "重试", onClick: onRetryLoad } : undefined}
            className="m-3"
          />
        ) : null}
        <div className="holdings-hero holdings-summary border-b border-[var(--line-strong)] px-4 pb-5 pt-4 sm:px-6 sm:pt-5">
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <div className="min-w-0">
              <div className="flex min-h-11 items-center">
                <div className="text-[13px] font-semibold text-[var(--muted)]">总资产</div>
                <button
                  type="button"
                  onClick={() => {
                    setAmountsHidden((current) => {
                      const next = !current;
                      saveAmountsHidden(next);
                      return next;
                    });
                  }}
                  className={SUMMARY_ICON_BTN}
                  title={amountsHidden ? "显示金额" : "隐藏金额"}
                  aria-label={amountsHidden ? "显示金额" : "隐藏金额"}
                >
                  {amountsHidden ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
                <button
                  type="button"
                  onClick={() => setLedgerOpen(true)}
                  className={SUMMARY_ICON_BTN}
                  title="交易记录"
                  aria-label="查看交易记录"
                >
                  <Receipt size={16} />
                </button>
              </div>
              <div className="kpi-value mt-1 break-all text-[clamp(1.85rem,10vw,2.15rem)] leading-none">
                {formatBalance(totalAssets, amountsHidden)}
              </div>
              {refreshError ? (
                <div role="alert" className="mt-2 text-xs text-rose-700">
                  {refreshError}
                </div>
              ) : refreshNotice?.tone === "amber" ? (
                // 每一行已经带「估值」角标了，这里再用两行文字复述一遍同一件事纯属噪音。
                // 收成一个可展开的口径说明：需要知道来源的人点开就有，其他人不被打扰。
                <MethodologyNote label="部分为估值" className="mt-2">
                  这些基金没有匹配到真实关联板块行情，当日涨跌改用天天基金净值估值补位，
                  行内以「估值」角标标出。估值刷新更快但不等同于真实板块行情。
                </MethodologyNote>
              ) : null}
            </div>
            <div className="flex w-full items-start justify-between sm:w-auto sm:justify-self-end">
              <button
                type="button"
                onClick={() => void refresh(true, "accurate")}
                disabled={isRefreshing}
                className={`${SUMMARY_ICON_BTN} disabled:opacity-50`}
                title="刷新板块涨跌"
                aria-label={isRefreshing ? "正在刷新板块涨跌" : "刷新板块涨跌"}
              >
                <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
              </button>
              <button
                type="button"
                onClick={() => onOpenAnalysis?.()}
                className="min-w-0 text-right transition hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]"
                title="查看盈亏分析"
                aria-label={
                  quoteTradeDate
                    ? `查看盈亏分析，当日收益 ${quoteTradeDate}`
                    : "查看盈亏分析"
                }
              >
                <div className="flex h-11 items-center justify-end gap-0.5 text-[13px] font-semibold text-[var(--muted)]">
                  <span className="whitespace-nowrap">
                    当日收益
                    {quoteTradeDate ? ` ${quoteTradeDate}` : ""}
                  </span>
                  {allOfficialDaily ? <UpdatedBadge className="ml-1 inline-flex px-1.5" /> : null}
                  <ChevronRight
                    size={14}
                    strokeWidth={2.4}
                    className="shrink-0 text-slate-400"
                    aria-hidden
                  />
                </div>
                <div
                  className={`font-display text-xl font-extrabold tabular-nums ${cnProfitClass(dailyProfit)}`}
                >
                  {formatSignedMoney(dailyProfit)}
                </div>
              </button>
            </div>
          </div>
          {pendingTxCount > 0 ? (
            <button
              type="button"
              onClick={() => setLedgerOpen(true)}
              className="mt-3 flex min-h-11 w-full items-center justify-between gap-2 rounded-xl border border-[var(--warn-border)] bg-[var(--warn-bg)] px-3 py-2 text-left"
            >
              <span className="text-xs font-bold text-[var(--warn-fg)]">
                {pendingTxCount} 笔交易待确认，暂不计收益
              </span>
              <span className="shrink-0 text-[11px] font-semibold text-[var(--warn-icon)]">查看</span>
            </button>
          ) : null}
        </div>

        {/* 这里曾经有一条通栏「行情日 {quoteTradeDate}」。同一个日期在上方
            「当日收益 {quoteTradeDate}」里已经出现过一次，通栏条只是把它重复一遍，
            还额外吃掉一行高度和一道分隔线。删掉。 */}

        <div
          ref={tableRef}
          className={`holdings-ledger-table${metricsScrollbarVisible ? " is-metrics-scrolling" : ""}`}
        >
        <div className="holdings-fund-head">基金</div>
        <div
          className="relative min-w-0"
          onMouseEnter={() => {
            metricsHoveringRef.current = true;
            setMetricsScrollbarVisible(true);
          }}
          onMouseLeave={() => {
            metricsHoveringRef.current = false;
            setMetricsScrollbarVisible(false);
          }}
        >
          <div
            className="holdings-metrics-scroll holdings-metrics-scroll--header"
            data-holdings-metrics-scroll
            data-testid="desktop-holdings-header"
            onScroll={handleMetricsScroll}
          >
            <div className="holdings-metrics-track text-[10px] font-bold text-slate-500">
              {HOLDINGS_METRIC_COLUMNS.map((column) => (
                <SortableColumnHeader
                  key={column.key}
                  label={column.label}
                  date={columnDate}
                  columnKey={column.key}
                  activeSortKey={sortKey}
                  sortDir={sortDir}
                  onSort={() => handleSort(column.key)}
                />
              ))}
            </div>
          </div>
          {canScrollMetricsLeft ? (
            <button
              type="button"
              className="holdings-metrics-nudge holdings-metrics-nudge--left"
              aria-label="查看前几列"
              onClick={() => scrollMetricsBy(-(headerMetricsNode()?.clientWidth || 168))}
            >
              <ChevronLeft size={16} strokeWidth={2.4} />
            </button>
          ) : null}
          {canScrollMetricsRight ? (
            <button
              type="button"
              className="holdings-metrics-nudge holdings-metrics-nudge--right"
              aria-label="查看后几列"
              onClick={() => scrollMetricsBy(headerMetricsNode()?.clientWidth || 168)}
            >
              <ChevronRight size={16} strokeWidth={2.4} />
            </button>
          ) : null}
        </div>

        <ul className="holdings-ledger contents">
          {sortedHoldings.map((holding, rowIndex) => {
            const unsettledOnly = isUnsettledPreviewHolding(holding);
            const pendingBuy = pendingBuyAmount(holding);
            const daily = unsettledOnly ? null : getDailyProfit(holding);
            const estimatedDailyReturn = unsettledOnly
              ? null
              : getEstimatedDailyReturnPercent(holding);
            const holdingProfit = unsettledOnly ? null : getEstimatedHoldingProfit(holding);
            const holdingReturn = unsettledOnly ? null : getEstimatedHoldingReturnPercent(holding);
            const dailyIsEstimated = isDailyProfitEstimated(holding);
            const profitAccrualDeferred = holding.profit_accrual_deferred === true;
            const isOfficialDaily = holdingHasCurrentOfficialNav(holding, sessionKind);
            const showDailyApprox =
              !isOfficialDaily &&
              (dailyIsEstimated ||
                holding.daily_return_is_estimated === true ||
                holding.daily_return_percent_source === "holdings_estimate");
            const sectorReturn = unsettledOnly ? null : resolveSectorBoardReturnPercent(holding);
            const sectorMeta = sectorMetaByFundCode[holding.fund_code] as SectorQuoteMeta | undefined;
            const sectorLabel = unsettledOnly ? "—" : holdingDisplaySectorLabel(holding, sectorMeta);
            const settledAmount = getSettledHoldingAmount(holding);
            const amountText = unsettledOnly ? pendingBuy : settledAmount;
            const weight = unsettledOnly ? null : computeHoldingWeight(holding, totalAssets);
            const shares = unsettledOnly ? null : getHoldingShares(holding);
            const unitCost = unsettledOnly ? null : getHoldingUnitCost(holding);
            const holdingDays = unsettledOnly ? null : getHoldingDays(holding);
            const holdingAmountLabel = amountsHidden
              ? "持有金额已隐藏"
              : unsettledOnly
                ? `在途 ${formatMoney(pendingBuy)}，暂不计收益`
                : `持有金额 ${formatMoney(settledAmount)}`;
            const rowAriaLabel = [
              holding.fund_name,
              holdingAmountLabel,
              `${dailyColumnLabel}收益 ${daily != null ? formatSignedMoney(daily) : "暂无"}`,
              estimatedDailyReturn != null ? formatSignedPercent(estimatedDailyReturn) : null,
              `板块涨跌 ${formatSignedPercent(sectorReturn)}`,
              sectorLabel !== "—" ? sectorLabel : null,
              `持有收益 ${formatSignedMoney(holdingProfit)}`,
              holdingReturn != null ? formatSignedPercent(holdingReturn) : null,
              `持仓占比 ${formatPlainPercent(weight)}`,
              `持有份额 ${shares != null ? formatPlainMoney(shares) : "暂无"}`,
              `持有成本 ${formatHoldingUnitCost(unitCost)}`,
              `持有天数 ${formatHoldingDays(holdingDays)}`,
            ]
              .filter(Boolean)
              .join("，");
            return (
              <li key={`${holdingIdentityKey(holding)}-${rowIndex}`} className="contents">
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
                  className="holding-ledger-row"
                >
                  <div className="holdings-fund-cell">
                    <div className="min-w-0">
                      <div className="line-clamp-2 break-words text-sm font-bold leading-5 text-slate-900 sm:truncate sm:text-[13px] sm:leading-tight">
                        {holding.fund_name}
                      </div>
                      {unsettledOnly || holding.has_in_progress_transactions || (isOfficialDaily && !unsettledOnly) ? (
                        <div className="mt-0.5 flex flex-wrap items-center gap-1">
                          {unsettledOnly || holding.has_in_progress_transactions ? (
                            <span className="shrink-0 rounded border border-[var(--warn-border)] bg-[var(--warn-bg)] px-1 py-0 text-[9px] font-bold leading-4 text-[var(--warn-icon)]">
                              待确认
                            </span>
                          ) : null}
                          {isOfficialDaily && !unsettledOnly ? (
                            <UpdatedBadge className="!px-0.5 !py-0 !text-[9px]" />
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                    {!amountsHidden ? (
                      <div className="mt-0.5 text-[10px] text-slate-500 tabular-nums">
                        {unsettledOnly ? `在途 ${formatYuan(amountText)}` : formatYuan(amountText)}
                        {!unsettledOnly && pendingBuy > 0 ? ` · 在途 ${formatYuan(pendingBuy)}` : ""}
                      </div>
                    ) : null}
                  </div>

                  <div
                    className="holdings-metrics-scroll"
                    data-holdings-metrics-scroll
                    onScroll={handleMetricsScroll}
                  >
                    <div className="holdings-metrics-track">
                      <div
                        className="holdings-metric-cell"
                        title={
                          unsettledOnly
                            ? "交易已导入，份额尚未确认，暂不计收益"
                            : profitAccrualDeferred
                            ? "份额待确认，次交易日起计收益（与支付宝一致）"
                            : isOfficialDaily
                            ? "官方净值已公布"
                            : holding.daily_return_percent_source === "holdings_estimate"
                            ? "季报重仓股加权估算，非正式净值"
                            : "板块或指数涨跌估算"
                        }
                      >
                        <div className={`whitespace-nowrap text-xs font-black tabular-nums sm:text-[13px] ${cnProfitClass(daily)}`}>
                          {daily != null ? formatSignedMoney(daily) : "—"}
                        </div>
                        {estimatedDailyReturn != null ? (
                          <div className={`mt-0.5 whitespace-nowrap text-[10px] font-semibold tabular-nums ${cnProfitClass(estimatedDailyReturn)}`}>
                            {showDailyApprox ? "≈" : ""}
                            {formatSignedPercent(estimatedDailyReturn)}
                          </div>
                        ) : null}
                      </div>

                      <div
                        className="holdings-metric-cell"
                        title={
                          sectorLabel !== "—"
                            ? isEstimateFallbackMeta(sectorMeta)
                              ? `${sectorLabel}（无真实关联板块行情，当前用天天基金净值估值代替）`
                              : sectorLabel
                            : undefined
                        }
                      >
                        <div className={`whitespace-nowrap text-xs font-black tabular-nums sm:text-[13px] ${cnProfitClass(sectorReturn)}`}>
                          {formatSignedPercent(sectorReturn)}
                        </div>
                        {sectorLabel !== "—" ? (
                          <div className="mt-0.5 flex min-w-0 items-center justify-center gap-1">
                            {isEstimateFallbackMeta(sectorMeta) ? (
                              <span className="shrink-0 rounded border border-amber-200 bg-amber-50 px-1 py-0 text-[8px] font-bold leading-4 text-amber-600">
                                估值
                              </span>
                            ) : null}
                            <span className="truncate text-[10px] font-semibold text-slate-500">{sectorLabel}</span>
                          </div>
                        ) : null}
                      </div>

                      <div className="holdings-metric-cell">
                        <div className={`whitespace-nowrap text-xs font-black tabular-nums sm:text-[13px] ${cnProfitClass(holdingProfit)}`}>
                          {formatSignedMoney(holdingProfit)}
                        </div>
                        {holdingReturn != null ? (
                          <div className={`mt-0.5 whitespace-nowrap text-[10px] font-semibold tabular-nums ${cnProfitClass(holdingReturn)}`}>
                            {isHoldingReturnEstimated(holding) ? "≈" : ""}
                            {formatSignedPercent(holdingReturn)}
                          </div>
                        ) : null}
                      </div>

                      <div className="holdings-metric-cell">
                        <div className="whitespace-nowrap text-xs font-black tabular-nums text-slate-800 sm:text-[13px]">
                          {amountsHidden ? "****" : formatPlainPercent(weight)}
                        </div>
                      </div>
                      <div className="holdings-metric-cell">
                        <div className="whitespace-nowrap text-xs font-black tabular-nums text-[var(--brand)] sm:text-[13px]">
                          {amountsHidden ? "****" : shares != null ? formatPlainMoney(shares) : "—"}
                        </div>
                      </div>
                      <div className="holdings-metric-cell">
                        <div className="whitespace-nowrap text-xs font-black tabular-nums text-[var(--brand)] sm:text-[13px]">
                          {amountsHidden ? "****" : formatHoldingUnitCost(unitCost)}
                        </div>
                      </div>
                      <div className="holdings-metric-cell">
                        <div className="whitespace-nowrap text-xs font-semibold tabular-nums text-slate-600 sm:text-[13px]">
                          {formatHoldingDays(holdingDays)}
                        </div>
                      </div>
                    </div>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
        </div>

        {onAddHolding ? (
          <div className="flex border-t border-slate-100">
            <button
              type="button"
              onClick={onAddHolding}
              className="flex min-h-11 flex-1 items-center justify-center gap-1.5 bg-white px-2 py-2.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--brand)]"
            >
              <Plus size={16} />
              同步持仓
            </button>
            {onBatchTransaction ? (
              <button
                type="button"
                onClick={onBatchTransaction}
                className="flex min-h-11 flex-1 items-center justify-center gap-1.5 border-l border-slate-100 bg-white px-2 py-2.5 text-sm font-bold text-[var(--brand)] transition hover:bg-[var(--brand-soft)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--brand)]"
              >
                <ArrowLeftRight size={16} />
                导入交易
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
      {ledgerOpen ? (
        <HoldingsTransactionLedgerModal
          onClose={() => setLedgerOpen(false)}
          onDeleteTransaction={onDeleteTransaction}
        />
      ) : null}
    </section>
  );
}

