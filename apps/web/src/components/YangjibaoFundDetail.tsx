"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Loader2,
  Pencil,
  RefreshCw,
  X,
} from "lucide-react";
import type { Holding, HoldingDetail, PortfolioSummary, SectorQuoteMeta } from "@/lib/api";
import {
  fetchHoldingDetail,
  fetchSectorIntraday,
  fetchTradingSession,
  updateFundProfile,
  updateFundProfilePurchaseDate,
} from "@/lib/api";
import { FundCodeEditModal, isProvisionalFundCode } from "@/components/FundCodeEditModal";
import { IntradayPercentChart } from "@/components/IntradayPercentChart";
import { PerformanceTrendPanel } from "@/components/PerformanceTrendPanel";
import {
  resolveInitialPurchaseDate,
  todayIsoDate,
  WheelDatePicker,
} from "@/components/WheelDatePicker";
import {
  cnProfitClass,
  computeCostBasis,
  computeDailyProfit,
  computeHoldingWeight,
  formatPlainMoney,
  formatPlainPercent,
  formatSignedMoney,
  formatSignedPercent,
  resolveSectorBoardReturnPercent,
} from "@/lib/holdingMetrics";
import {
  getEstimatedDailyReturnPercent,
  getEstimatedHoldingProfit,
  getEstimatedHoldingReturnPercent,
  getSettledHoldingAmount,
} from "@/lib/holdingDisplay";
import { HoldingModifyModal } from "@/components/HoldingModifyModal";
import { SingleFundTransactionModal } from "@/components/SingleFundTransactionModal";
import { holdingDisplaySectorLabel, holdingRelatedBoardLabel, resolveIntradayQuery } from "@/lib/profileSector";
import { buildClientCacheKey, readClientCache, writeClientCache } from "@/lib/clientCache";
import { isEstimateFallbackMeta } from "@/lib/sectorQuoteStatus";
import { formatTradeDateShort } from "@/lib/tradeDateLabel";

type DetailTab = "sector" | "performance" | "profit";

const PROVENANCE_LABEL: Record<string, string> = {
  ocr_detail: "详情 OCR",
  first_seen: "按首次记录日",
  nav: "净值推算",
  snapshot: "历史快照",
  computed: "公式估算",
  profile: "自动维护",
  akshare: "AkShare 匹配",
  user: "手动设置",
};

type YangjibaoFundDetailProps = {
  holding: Holding;
  holdingIndex: number;
  holdings: Holding[];
  portfolioSummary?: PortfolioSummary | null;
  sectorMeta?: SectorQuoteMeta;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onHoldingResolved?: (index: number, holding: Holding) => void;
  onFundCodeUpdated?: (index: number, holding: Holding) => void | Promise<void>;
  onDeleteHolding?: (index: number) => void;
  onPortfolioUpdated?: (holdings: Holding[]) => void | Promise<void>;
};

function HeaderStat({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="px-2 py-2.5 text-center">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div
        className={`mt-1 text-lg font-black tabular-nums leading-none ${valueClass ?? "text-slate-900"}`}
      >
        {value}
      </div>
    </div>
  );
}

function GridStat({
  label,
  value,
  valueClass = "text-slate-900",
  onClick,
  clickable,
}: {
  label: string;
  value: string;
  valueClass?: string;
  onClick?: () => void;
  clickable?: boolean;
}) {
  const content = (
    <>
      <div className="text-[11px] text-slate-400">{label}</div>
      <div
        className={`mt-1.5 text-[15px] font-black tabular-nums leading-tight ${valueClass} ${
          clickable ? "underline decoration-dotted decoration-slate-300 underline-offset-2" : ""
        }`}
      >
        {value}
      </div>
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="w-full px-2 py-3 text-center transition hover:bg-slate-100/70 active:bg-slate-100"
        title="点击设置首次购入日期"
      >
        {content}
      </button>
    );
  }

  return <div className="px-2 py-3 text-center">{content}</div>;
}

function sourceHint(provenance: Record<string, string>, field: string) {
  const source = provenance[field];
  if (!source) {
    return undefined;
  }
  return PROVENANCE_LABEL[source] ?? source;
}

export function YangjibaoFundDetail({
  holding,
  holdingIndex,
  holdings,
  portfolioSummary,
  sectorMeta,
  onClose,
  onNavigate,
  onHoldingResolved,
  onFundCodeUpdated,
  onDeleteHolding,
  onPortfolioUpdated,
}: YangjibaoFundDetailProps) {
  const [tab, setTab] = useState<DetailTab>("sector");
  const [detail, setDetail] = useState<HoldingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [intradayLoading, setIntradayLoading] = useState(false);
  const [intradayPoints, setIntradayPoints] = useState<Array<{ time: string; percent: number }>>([]);
  const [intradayClosePercent, setIntradayClosePercent] = useState<number | null>(null);
  const [intradayNote, setIntradayNote] = useState<string | null>(null);
  const [intradayRefreshing, setIntradayRefreshing] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [holdingsExpanded, setHoldingsExpanded] = useState(false);
  const [quoteTradeDate, setQuoteTradeDate] = useState<string | null>(null);
  const [purchaseDateSaving, setPurchaseDateSaving] = useState(false);
  const [purchaseDateError, setPurchaseDateError] = useState<string | null>(null);
  const [purchaseDatePickerOpen, setPurchaseDatePickerOpen] = useState(false);
  const [fundCodeEditOpen, setFundCodeEditOpen] = useState(false);
  const [fundCodeSaving, setFundCodeSaving] = useState(false);
  const [fundCodeError, setFundCodeError] = useState<string | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [modifyOpen, setModifyOpen] = useState(false);
  const [txDirection, setTxDirection] = useState<"buy" | "sell" | null>(null);
  const [intradayForceSeq, setIntradayForceSeq] = useState(0);
  const intradayRequestSeq = useRef(0);

  const activeHolding = detail?.holding ?? holding;
  const provenance = detail?.provenance ?? {};

  const totalAssets =
    portfolioSummary?.total_assets ??
    (holdings.reduce((sum, item) => sum + item.holding_amount, 0) || null);

  const holdingReturn = getEstimatedHoldingReturnPercent(activeHolding);
  const holdingProfit = getEstimatedHoldingProfit(activeHolding);
  const dailyProfit = computeDailyProfit(activeHolding);
  const settledAmount = getSettledHoldingAmount(activeHolding);
  const costBasis = computeCostBasis(activeHolding);
  const weight = computeHoldingWeight(activeHolding, totalAssets);

  const shares = detail?.holding_shares ?? null;
  const unitCost = detail?.holding_cost ?? null;
  const yesterdayProfit = detail?.yesterday_profit ?? null;
  const holdingDays = detail?.holding_days ?? null;
  const firstPurchaseDate = detail?.first_purchase_date ?? "";
  const canEditPurchaseDate =
    detail?.fund_code_resolved === true && activeHolding.fund_code !== "000000";
  const yearReturn = detail?.year_return_percent ?? null;

  const quoteLabel = holdingDisplaySectorLabel(activeHolding, sectorMeta);
  const sectorReturn = resolveSectorBoardReturnPercent(activeHolding);
  // 盘中优先用分时末点（与曲线同源），避免板块刷新缓存与分时不同步
  const displaySectorReturn =
    tab === "sector" && intradayClosePercent != null ? intradayClosePercent : sectorReturn;
  const dataSourceLabel = isEstimateFallbackMeta(sectorMeta)
    ? "估值兜底"
    : sectorMeta?.provider === "eastmoney-kline" || sectorMeta?.source === "live"
      ? "东财"
      : "数据源";
  const displayDailyReturn =
    activeHolding.daily_return_percent ?? getEstimatedDailyReturnPercent(activeHolding);

  const intradayQuery = useMemo(
    () => resolveIntradayQuery(activeHolding, sectorMeta),
    [activeHolding, sectorMeta],
  );

  const canGoPrev = holdingIndex > 0;
  const canGoNext = holdingIndex < holdings.length - 1;

  async function handlePurchaseDateChange(nextDate: string) {
    if (!canEditPurchaseDate || purchaseDateSaving) {
      return;
    }
    const normalized = nextDate || null;
    if (normalized === (detail?.first_purchase_date ?? null)) {
      return;
    }
    setPurchaseDateSaving(true);
    setPurchaseDateError(null);
    try {
      await updateFundProfilePurchaseDate(activeHolding.fund_code, normalized);
      const result = await fetchHoldingDetail({
        holdings,
        index: holdingIndex,
        portfolio_summary: portfolioSummary,
        sector_quote_meta: sectorMeta,
      });
      setDetail(result);
      setPurchaseDatePickerOpen(false);
    } catch (error) {
      setPurchaseDateError(error instanceof Error ? error.message : "保存购入日期失败");
    } finally {
      setPurchaseDateSaving(false);
    }
  }

  async function handleFundCodeSave(nextCode: string, nextName: string) {
    if (fundCodeSaving) {
      return;
    }
    const oldCode = activeHolding.fund_code;
    if (nextCode === oldCode && nextName === activeHolding.fund_name) {
      setFundCodeEditOpen(false);
      return;
    }

    setFundCodeSaving(true);
    setFundCodeError(null);
    try {
      if (oldCode !== "000000") {
        try {
          await updateFundProfile(oldCode, {
            fund_code: nextCode !== oldCode ? nextCode : undefined,
            fund_name: nextName !== activeHolding.fund_name ? nextName : undefined,
          });
        } catch (error) {
          const message = error instanceof Error ? error.message : "更新档案失败";
          if (!message.includes("404") && !message.includes("不存在")) {
            throw error;
          }
        }
      }

      const updatedHolding: Holding = {
        ...activeHolding,
        fund_code: nextCode,
        fund_name: nextName,
      };
      await onFundCodeUpdated?.(holdingIndex, updatedHolding);
      onHoldingResolved?.(holdingIndex, updatedHolding);

      const result = await fetchHoldingDetail({
        holdings: holdings.map((item, index) => (index === holdingIndex ? updatedHolding : item)),
        index: holdingIndex,
        portfolio_summary: portfolioSummary,
        sector_quote_meta: sectorMeta,
      });
      setDetail(result);
      setFundCodeEditOpen(false);
    } catch (error) {
      setFundCodeError(error instanceof Error ? error.message : "保存基金代码失败");
    } finally {
      setFundCodeSaving(false);
    }
  }

  const needsCodeAttention =
    activeHolding.fund_code === "000000" || isProvisionalFundCode(activeHolding.fund_code);

  function handleDeleteHolding() {
    if (!onDeleteHolding) {
      return;
    }
    setDeleteConfirmOpen(false);
    onDeleteHolding(holdingIndex);
    onClose();
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    const detailCacheKey = buildClientCacheKey(
      "holding-detail",
      holdings[holdingIndex]?.fund_code,
    );
    const cachedDetail = readClientCache<HoldingDetail>(detailCacheKey, 5 * 60 * 1000);
    if (cachedDetail) {
      setDetail(cachedDetail);
      setDetailLoading(false);
    } else {
      setDetailLoading(true);
    }
    setDetailError(null);
    void fetchHoldingDetail({
      holdings,
      index: holdingIndex,
      portfolio_summary: portfolioSummary,
      sector_quote_meta: sectorMeta,
    })
      .then((result) => {
        if (cancelled) {
          return;
        }
        writeClientCache(detailCacheKey, result);
        setDetail(result);
        if (
          result.fund_code_resolved &&
          result.holding.fund_code !== holdings[holdingIndex]?.fund_code
        ) {
          onHoldingResolved?.(holdingIndex, result.holding);
        }
      })
      .catch((error) => {
        if (!cancelled && !cachedDetail) {
          setDetail(null);
          setDetailError(error instanceof Error ? error.message : "基金详情加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setDetailLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [holdingIndex, holdings, portfolioSummary, sectorMeta, onHoldingResolved]);

  useEffect(() => {
    if (tab !== "sector") {
      return;
    }
    if (!intradayQuery) {
      setIntradayPoints([]);
      setIntradayClosePercent(null);
      setIntradayNote("暂无板块映射，请先在持仓页刷新板块或上传详情截图建档");
      setIntradayLoading(false);
      setIntradayRefreshing(false);
      return;
    }

    const requestId = ++intradayRequestSeq.current;
    setIntradayPoints([]);
    setIntradayClosePercent(null);
    setIntradayNote(null);
    setIntradayLoading(true);
    setIntradayRefreshing(false);

    const applyIntraday = (result: {
      points: Array<{ time: string; percent: number }>;
      note?: string | null;
      close_change_percent?: number | null;
    }) => {
      setIntradayPoints(result.points);
      setIntradayNote(result.note ?? null);
      const lastPoint = result.points[result.points.length - 1]?.percent;
      const close =
        result.close_change_percent != null
          ? result.close_change_percent
          : lastPoint != null
            ? lastPoint
            : null;
      setIntradayClosePercent(close);
    };

    void (async () => {
      let showedCache = false;
      const forceRefresh = intradayForceSeq > 0;

      try {
        const cached = await fetchSectorIntraday(
          intradayQuery,
          forceRefresh ? { forceRefresh: true } : undefined,
        );
        if (requestId !== intradayRequestSeq.current) {
          return;
        }
        if (cached.points.length >= 2) {
          applyIntraday(cached);
          showedCache = true;
          setIntradayLoading(false);
          if (!forceRefresh) {
            return;
          }
        } else if (cached.note) {
          setIntradayNote(cached.note);
        }
      } catch {
        if (requestId !== intradayRequestSeq.current) {
          return;
        }
      }

      if (requestId !== intradayRequestSeq.current) {
        return;
      }
      if (showedCache && !forceRefresh) {
        setIntradayLoading(false);
        return;
      }

      setIntradayRefreshing(true);

      try {
        const fresh = await fetchSectorIntraday(intradayQuery, { forceRefresh: true });
        if (requestId !== intradayRequestSeq.current) {
          return;
        }
        if (fresh.points.length >= 2) {
          applyIntraday(fresh);
        } else if (!showedCache) {
          applyIntraday(fresh);
        }
      } catch {
        if (requestId !== intradayRequestSeq.current) {
          return;
        }
        if (!showedCache) {
          setIntradayPoints([]);
          setIntradayNote("分时数据暂不可用");
        }
      } finally {
        if (requestId === intradayRequestSeq.current) {
          setIntradayRefreshing(false);
          setIntradayLoading(false);
        }
      }
    })();

    return () => {
      intradayRequestSeq.current += 1;
    };
  }, [tab, intradayQuery, intradayForceSeq]);

  useEffect(() => {
    void fetchTradingSession()
      .then((session) => setQuoteTradeDate(formatTradeDateShort(session.effective_trade_date)))
      .catch(() => setQuoteTradeDate(null));
  }, []);

  const tradeDateLabel = quoteTradeDate ?? "—";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-end justify-center bg-slate-950/50 p-0 sm:items-center sm:p-4"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="fund-detail-title"
        className="flex max-h-[min(100dvh,920px)] w-full max-w-lg flex-col overflow-y-auto overscroll-contain bg-white shadow-2xl sm:max-h-[min(92dvh,860px)] sm:rounded-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sticky top-0 z-10 shrink-0">
          <header
            className="px-3 pb-3 pt-3 text-white shadow-sm"
            style={{ background: "linear-gradient(135deg, var(--brand) 0%, var(--brand-strong) 100%)" }}
          >
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-8 w-8 items-center justify-center rounded-full hover:bg-white/15"
                aria-label="返回"
              >
                <ChevronLeft size={20} />
              </button>
              <div className="min-w-0 flex-1 text-center">
                <div id="fund-detail-title" className="truncate text-sm font-bold leading-tight">
                  {activeHolding.fund_name}
                </div>
                <div className="text-[10px] text-white/80">
                  <button
                    type="button"
                    onClick={() => {
                      setFundCodeError(null);
                      setFundCodeEditOpen(true);
                    }}
                    className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 transition hover:bg-white/15 ${
                      needsCodeAttention ? "bg-amber-400/25 text-amber-100" : ""
                    }`}
                    title="修正基金代码"
                  >
                    <span className="tabular-nums">{activeHolding.fund_code}</span>
                    <Pencil size={10} />
                  </button>
                  {detail?.fund_code_source === "akshare" ? " · 已自动匹配" : null}
                  {needsCodeAttention ? " · 建议修正代码" : null}
                </div>
              </div>
              <div className="flex items-center gap-0.5">
                <button
                  type="button"
                  disabled={!canGoPrev}
                  onClick={() => onNavigate(holdingIndex - 1)}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-full hover:bg-white/15 disabled:opacity-30"
                  aria-label="上一只"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  type="button"
                  disabled={!canGoNext}
                  onClick={() => onNavigate(holdingIndex + 1)}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-full hover:bg-white/15 disabled:opacity-30"
                  aria-label="下一只"
                >
                  <ChevronRight size={16} />
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-full hover:bg-white/15"
                  aria-label="关闭"
                >
                  <X size={16} />
                </button>
              </div>
            </div>
          </header>

          <div className="border-b border-slate-100 bg-white">
            <div className="grid grid-cols-3 divide-x divide-slate-100">
              <HeaderStat
                label={`当日涨幅 ${tradeDateLabel}`}
                value={formatSignedPercent(displayDailyReturn)}
                valueClass={cnProfitClass(displayDailyReturn)}
              />
              <HeaderStat
                label="近1年"
                value={
                  yearReturn != null ? formatSignedPercent(yearReturn) : detailLoading ? "…" : "—"
                }
                valueClass={cnProfitClass(yearReturn)}
              />
              <HeaderStat
                label="持仓占比"
                value={weight != null ? formatPlainPercent(weight) : "—"}
              />
            </div>
          </div>
        </div>

        {detailError ? (
          <div className="border-b border-rose-100 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">
            {detailError}
          </div>
        ) : null}

        <div className="border-b border-slate-100 bg-slate-50">
          {holdingsExpanded ? (
            <div className="divide-y divide-slate-100/80">
              <div className="grid grid-cols-3 divide-x divide-slate-100">
                <GridStat label="持有金额" value={formatPlainMoney(settledAmount)} />
                <GridStat
                  label="持有份额"
                  value={shares != null ? formatPlainMoney(shares) : detailLoading ? "…" : "—"}
                />
                <GridStat
                  label="持仓占比"
                  value={weight != null ? formatPlainPercent(weight) : "—"}
                />
              </div>
              <div className="grid grid-cols-3 divide-x divide-slate-100">
                <GridStat
                  label="持有收益"
                  value={formatSignedMoney(holdingProfit)}
                  valueClass={cnProfitClass(holdingProfit)}
                />
                <GridStat
                  label="持有收益率"
                  value={formatSignedPercent(holdingReturn)}
                  valueClass={cnProfitClass(holdingReturn)}
                />
                <GridStat
                  label="持仓成本"
                  value={unitCost != null ? unitCost.toFixed(4) : detailLoading ? "…" : "—"}
                />
              </div>
              <div className="grid grid-cols-3 divide-x divide-slate-100">
                <GridStat
                  label="当日收益"
                  value={formatSignedMoney(dailyProfit)}
                  valueClass={cnProfitClass(dailyProfit)}
                />
                <GridStat
                  label="昨日收益"
                  value={
                    yesterdayProfit != null ? formatSignedMoney(yesterdayProfit) : detailLoading ? "…" : "—"
                  }
                  valueClass={cnProfitClass(yesterdayProfit)}
                />
                <GridStat
                  label="持有天数"
                  value={holdingDays != null ? `${holdingDays}` : detailLoading ? "…" : "—"}
                  clickable={canEditPurchaseDate}
                  onClick={
                    canEditPurchaseDate
                      ? () => {
                          setPurchaseDateError(null);
                          setPurchaseDatePickerOpen(true);
                        }
                      : undefined
                  }
                />
              </div>
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => setHoldingsExpanded((current) => !current)}
            className="flex w-full items-center justify-center py-1.5 text-slate-300 transition hover:text-slate-500"
            aria-label={holdingsExpanded ? "收起持仓明细" : "展开持仓明细"}
          >
            {holdingsExpanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
        </div>

        <div className="border-b border-slate-100 bg-white px-4">
          <div className="flex gap-6 text-[15px] font-bold">
            {(
              [
                ["sector", "关联板块"],
                ["performance", "业绩走势"],
                ["profit", "我的收益"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={`border-b-[2.5px] py-3 transition ${
                  tab === id
                    ? "border-[var(--brand)] text-[var(--brand)]"
                    : "border-transparent text-slate-500"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="px-3 py-3">
          {tab === "sector" ? (
            <div>
              <div className="mb-1 flex items-center gap-2 border-b border-slate-100 pb-2 text-xs">
                <span className="shrink-0 text-slate-400">日期 {tradeDateLabel}</span>
                <span className="flex min-w-0 flex-1 items-center justify-center gap-0.5 truncate font-bold text-slate-800">
                  {quoteLabel}
                  <ChevronDown size={12} className="shrink-0 text-slate-400" />
                </span>
                <span
                  className={`shrink-0 text-sm font-black tabular-nums ${cnProfitClass(displaySectorReturn)}`}
                >
                  {formatSignedPercent(displaySectorReturn)}
                </span>
                <span className="flex shrink-0 items-center gap-1 text-[10px] text-slate-400">
                  {dataSourceLabel}
                  <button
                    type="button"
                    onClick={() => setIntradayForceSeq((value) => value + 1)}
                    disabled={intradayRefreshing || intradayLoading}
                    className="inline-flex items-center hover:text-slate-600 disabled:opacity-50"
                    title="刷新分时"
                    aria-label="刷新分时"
                  >
                    {intradayRefreshing ? (
                      <Loader2 size={10} className="animate-spin" />
                    ) : (
                      <RefreshCw size={10} />
                    )}
                  </button>
                </span>
              </div>
              {intradayLoading ? (
                <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">
                  <Loader2 size={18} className="mr-2 animate-spin" />
                  加载分时…
                </div>
              ) : intradayPoints.length < 2 ? (
                <div className="flex h-[200px] flex-col items-center justify-center gap-1 px-4 text-center text-sm text-slate-400">
                  <span>{intradayNote ?? "暂无分时数据"}</span>
                </div>
              ) : (
                <IntradayPercentChart points={intradayPoints} height={200} />
              )}
              <div className="mt-2 border-t border-slate-100">
                <div className="flex items-center justify-between gap-2 py-3 text-sm">
                  <span className="text-slate-500">关联板块</span>
                  <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
                    <span className="truncate font-bold text-slate-800">{quoteLabel}</span>
                    <span
                      className={`shrink-0 font-black tabular-nums ${cnProfitClass(displaySectorReturn)}`}
                    >
                      {formatSignedPercent(displaySectorReturn)}
                    </span>
                    <ChevronRight size={14} className="shrink-0 text-slate-300" />
                  </div>
                </div>
              </div>
              {intradayNote && intradayPoints.length >= 2 ? (
                <p className="pb-1 text-center text-[11px] text-slate-400">{intradayNote}</p>
              ) : null}
            </div>
          ) : null}

          {tab === "performance" ? (
            <PerformanceTrendPanel
              fundCode={activeHolding.fund_code}
              fundName={activeHolding.fund_name}
              costPrice={unitCost}
              enabled={detail?.fund_code_resolved === true}
            />
          ) : null}

          {tab === "profit" ? (
            <div className="space-y-2">
              <ProfitRow label="持有金额" value={`¥ ${formatPlainMoney(settledAmount)}`} />
              <ProfitRow
                label="持仓成本总额"
                value={costBasis != null ? `¥ ${formatPlainMoney(costBasis)}` : "—"}
              />
              <ProfitRow
                label="持有份额"
                value={shares != null ? formatPlainMoney(shares) : "—"}
                hint={sourceHint(provenance, "holding_shares")}
              />
              <ProfitRow
                label="单位成本"
                value={unitCost != null ? unitCost.toFixed(4) : "—"}
                hint={sourceHint(provenance, "holding_cost")}
              />
              <ProfitRow
                label="持有收益"
                value={formatSignedMoney(holdingProfit)}
                valueClass={cnProfitClass(holdingProfit)}
              />
              <ProfitRow
                label="昨日收益"
                value={yesterdayProfit != null ? formatSignedMoney(yesterdayProfit) : "—"}
                valueClass={cnProfitClass(yesterdayProfit)}
                hint={sourceHint(provenance, "yesterday_profit")}
              />
              <ProfitRow
                label="持有天数"
                value={holdingDays != null ? `${holdingDays} 天` : "—"}
                hint={sourceHint(provenance, "holding_days")}
              />
              {weight != null ? <ProfitRow label="占账户比例" value={formatPlainPercent(weight)} /> : null}
            </div>
          ) : null}
        </div>

        <footer className="sticky bottom-0 z-10 shrink-0 border-t border-slate-100 bg-white">
          <div className="grid grid-cols-3 divide-x divide-slate-100">
            <button
              type="button"
              onClick={onClose}
              className="flex flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
            >
              <ChevronLeft size={18} className="text-slate-500" />
              返回列表
            </button>
            <button
              type="button"
              onClick={() => setModifyOpen(true)}
              className="flex flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-[#2356e0] hover:bg-blue-50"
            >
              <Pencil size={18} className="text-[#2356e0]" />
              修改持仓
            </button>
            {onDeleteHolding ? (
              <button
                type="button"
                onClick={() => {
                  setDeleteConfirmOpen(true);
                }}
                className="flex flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-rose-600 hover:bg-rose-50"
              >
                删除该基金
              </button>
            ) : (
              <div />
            )}
          </div>
        </footer>

        {deleteConfirmOpen ? (
          <div
            className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/40 p-4"
            onClick={() => setDeleteConfirmOpen(false)}
            role="presentation"
          >
            <div
              className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl"
              onClick={(event) => event.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-labelledby="delete-holding-title"
            >
              <h3 id="delete-holding-title" className="text-base font-bold text-slate-900">
                删除该基金？
              </h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                将从当前账户汇总移除「{activeHolding.fund_name}」。基金档案与历史盈亏快照会保留，之后仍可重新添加。
              </p>
              <div className="mt-4 flex gap-2">
                <button
                  type="button"
                  onClick={() => setDeleteConfirmOpen(false)}
                  className="btn-secondary flex-1 !py-2.5"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={handleDeleteHolding}
                  className="flex-1 rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-rose-700"
                >
                  确认删除
                </button>
              </div>
            </div>
          </div>
        ) : null}

        <PurchaseDatePickerModal
          open={purchaseDatePickerOpen}
          firstPurchaseDate={firstPurchaseDate}
          holdingDays={holdingDays}
          holdingDaysSource={provenance.holding_days}
          hint={sourceHint(provenance, "holding_days")}
          saving={purchaseDateSaving}
          error={purchaseDateError}
          onClose={() => setPurchaseDatePickerOpen(false)}
          onDateChange={handlePurchaseDateChange}
        />

        <FundCodeEditModal
          open={fundCodeEditOpen}
          fundCode={activeHolding.fund_code}
          fundName={activeHolding.fund_name}
          saving={fundCodeSaving}
          error={fundCodeError}
          onClose={() => {
            if (!fundCodeSaving) {
              setFundCodeEditOpen(false);
              setFundCodeError(null);
            }
          }}
          onSave={handleFundCodeSave}
        />

        <HoldingModifyModal
          open={modifyOpen}
          holding={activeHolding}
          holdingDays={holdingDays}
          onClose={() => setModifyOpen(false)}
          onSaved={async (payload) => {
            await onPortfolioUpdated?.(payload.holdings);
            const result = await fetchHoldingDetail({
              holdings: payload.holdings,
              index: holdingIndex,
              portfolio_summary: portfolioSummary,
              sector_quote_meta: sectorMeta,
            });
            setDetail(result);
          }}
          onEditPurchaseDate={() => {
            setModifyOpen(false);
            if (canEditPurchaseDate) {
              setPurchaseDatePickerOpen(true);
            }
          }}
          onSyncBuy={() => {
            setModifyOpen(false);
            setTxDirection("buy");
          }}
          onSyncSell={() => {
            setModifyOpen(false);
            setTxDirection("sell");
          }}
        />

        <SingleFundTransactionModal
          open={txDirection != null}
          holding={activeHolding}
          direction={txDirection ?? "buy"}
          maxShares={shares ?? undefined}
          latestNav={detail?.latest_nav ?? undefined}
          navDateLabel={detail?.nav_date ?? undefined}
          onClose={() => setTxDirection(null)}
          onApplied={async (nextHoldings) => {
            await onPortfolioUpdated?.(nextHoldings);
            const result = await fetchHoldingDetail({
              holdings: nextHoldings,
              index: holdingIndex,
              portfolio_summary: portfolioSummary,
              sector_quote_meta: sectorMeta,
            });
            setDetail(result);
          }}
        />
      </div>
    </div>
  );
}

function ProfitRow({
  label,
  value,
  valueClass = "text-slate-900",
  hint,
}: {
  label: string;
  value: string;
  valueClass?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-slate-100 bg-slate-50/80 px-3 py-2">
      <div className="min-w-0">
        <div className="text-[11px] text-slate-500">{label}</div>
        {hint ? <div className="truncate text-[10px] text-slate-400">{hint}</div> : null}
      </div>
      <div className={`shrink-0 text-sm font-black tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}

function PurchaseDatePickerModal({
  open,
  firstPurchaseDate,
  holdingDays,
  holdingDaysSource,
  hint,
  saving,
  error,
  onClose,
  onDateChange,
}: {
  open: boolean;
  firstPurchaseDate: string;
  holdingDays: number | null;
  holdingDaysSource?: string;
  hint?: string;
  saving: boolean;
  error: string | null;
  onClose: () => void;
  onDateChange: (value: string) => void;
}) {
  const [draftDate, setDraftDate] = useState<string | null>(null);
  const [pickerSession, setPickerSession] = useState(0);

  useEffect(() => {
    if (!open) {
      setDraftDate(null);
      return;
    }
    const initialDate = resolveInitialPurchaseDate(
      holdingDays,
      firstPurchaseDate,
      holdingDaysSource,
    );
    setDraftDate(initialDate);
    setPickerSession((current) => current + 1);
  }, [firstPurchaseDate, holdingDays, holdingDaysSource, open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  if (!open || !draftDate) {
    return null;
  }

  const previewDays = draftDate
    ? Math.max(
        0,
        Math.floor(
          (Date.parse(`${todayIsoDate()}T00:00:00`) - Date.parse(`${draftDate}T00:00:00`)) /
            86_400_000,
        ),
      )
    : null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-slate-950/40 p-0 sm:items-center sm:p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-sm rounded-t-3xl bg-white p-5 shadow-2xl sm:rounded-2xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="purchase-date-title"
      >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-slate-200 sm:hidden" />
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 id="purchase-date-title" className="text-base font-bold text-slate-900">
              选择首次购入日期
            </h3>
            <p className="mt-1 text-xs text-slate-500">滑动选择年月日，保存后每天自动递增</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 text-center">
          <div className="text-[11px] text-slate-400">
            {previewDays != null ? "预计持有天数" : "当前持有天数"}
          </div>
          <div className="mt-1 text-2xl font-black tabular-nums text-slate-900">
            {(previewDays ?? holdingDays) != null ? `${previewDays ?? holdingDays} 天` : "—"}
          </div>
          {hint && !previewDays ? <div className="mt-1 text-[10px] text-slate-400">{hint}</div> : null}
        </div>

        <div className="mt-4">
          <WheelDatePicker
            key={pickerSession}
            value={draftDate}
            max={todayIsoDate()}
            onChange={setDraftDate}
          />
        </div>

        {error ? <p className="mt-2 text-xs font-medium text-rose-500">{error}</p> : null}

        <div className="mt-4 flex gap-2">
          {firstPurchaseDate ? (
            <button
              type="button"
              disabled={saving}
              onClick={() => onDateChange("")}
              className="flex-1 rounded-xl border border-slate-200 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              清除日期
            </button>
          ) : null}
          <button
            type="button"
            disabled={saving || !draftDate}
            onClick={() => onDateChange(draftDate)}
            className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[var(--brand)] py-2.5 text-sm font-semibold text-white hover:bg-[var(--brand-strong)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : null}
            完成
          </button>
        </div>
      </div>
    </div>
  );
}
