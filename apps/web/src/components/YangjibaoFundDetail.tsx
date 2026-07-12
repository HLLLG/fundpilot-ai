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
import type {
  Holding,
  HoldingAdjustmentPatch,
  HoldingDetail,
  ParsedTransaction,
  PortfolioSummary,
  SectorQuoteMeta,
} from "@/lib/api";
import {
  fetchHoldingDetail,
  fetchSectorIntraday,
  updateFundProfile,
  updateFundProfilePurchaseDate,
} from "@/lib/api";
import { hydrateTradingSession } from "@/lib/tradingSessionClient";
import { FundCodeEditModal, isProvisionalFundCode } from "@/components/FundCodeEditModal";
import { buildFlatIntradayPoints, IntradayPercentChart } from "@/components/IntradayPercentChart";
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
  findHoldingIndex,
  formatPlainMoney,
  formatPlainPercent,
  formatSignedMoney,
  formatSignedPercent,
  mergeSectorIntradayClose,
  navigableHoldings,
  resolveSectorBoardReturnPercent,
  type HoldingIdentity,
} from "@/lib/holdingMetrics";
import {
  getEstimatedDailyReturnPercent,
  getEstimatedHoldingProfit,
  getEstimatedHoldingReturnPercent,
  getSettledHoldingAmount,
} from "@/lib/holdingDisplay";
import { HoldingModifyModal } from "@/components/HoldingModifyModal";
import { SingleFundTransactionModal } from "@/components/SingleFundTransactionModal";
import {
  holdingDisplaySectorLabel,
  resolveIntradayFallbackQuery,
  resolveIntradayQuery,
} from "@/lib/profileSector";
import {
  readHoldingDetailCache,
  readIntradayCache,
  writeHoldingDetailCache,
  writeIntradayCache,
} from "@/lib/holdingDetailCache";
import { useAuth } from "@/components/AuthProvider";
import { isEstimateFallbackMeta } from "@/lib/sectorQuoteStatus";
import { formatTradeDateShort } from "@/lib/tradeDateLabel";
import { useDialogA11y } from "@/lib/useDialogA11y";

type DetailTab = "sector" | "performance" | "profit";

export type HoldingMutationResult = {
  holdings: Holding[];
  portfolioSummary?: PortfolioSummary | null;
};

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
  onNavigate: (target: HoldingIdentity) => void;
  onHoldingResolved?: (index: number, holding: Holding) => void;
  onFundCodeUpdated?: (index: number, holding: Holding) => void | Promise<void>;
  onDeleteHolding?: (index: number) => void;
  onAdjustHolding?: (
    fundCode: string,
    patch: HoldingAdjustmentPatch,
  ) => Promise<HoldingMutationResult | null>;
  onApplyTransaction?: (transaction: ParsedTransaction) => Promise<HoldingMutationResult | null>;
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
      <div className="text-[11px] text-slate-500">{label}</div>
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
      <div className="text-[11px] text-slate-500">{label}</div>
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
  onAdjustHolding,
  onApplyTransaction,
}: YangjibaoFundDetailProps) {
  const { user } = useAuth();
  const userId = user?.id ?? null;
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
  const mainCloseButtonRef = useRef<HTMLButtonElement>(null);
  const deleteCancelButtonRef = useRef<HTMLButtonElement>(null);
  const detailDialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose,
    initialFocusRef: mainCloseButtonRef,
  });
  const deleteDialogRef = useDialogA11y<HTMLDivElement>({
    open: deleteConfirmOpen,
    onClose: () => setDeleteConfirmOpen(false),
    initialFocusRef: deleteCancelButtonRef,
  });

  const activeHolding = detail?.holding ?? holding;
  const activeHoldingRef = useRef(activeHolding);
  const onHoldingResolvedRef = useRef(onHoldingResolved);
  const provenance = detail?.provenance ?? {};
  const selectedHolding = holdings[holdingIndex] ?? holding;
  const detailRequestKey = [
    holdingIndex,
    selectedHolding?.fund_code ?? "",
    selectedHolding?.fund_name ?? "",
    selectedHolding?.holding_amount ?? "",
    selectedHolding?.return_percent ?? "",
    selectedHolding?.holding_profit ?? "",
    selectedHolding?.holding_return_percent ?? "",
    selectedHolding?.settled_holding_amount ?? "",
    selectedHolding?.display_holding_amount ?? "",
  ].join("|");
  const detailInputsRef = useRef({
    holdings,
    portfolioSummary,
    sectorMeta,
    onHoldingResolved,
  });
  const detailTargetRef = useRef(selectedHolding ?? holding);

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
  // 分时接口拿不到真实序列（如缺 secid 的板块）时，用已确认可信的板块日涨跌画一条
  // 虚线水平线，好过一直显示"暂无分时数据"的空白占位——至少让用户知道我们确实有
  // 今天的涨跌幅，只是没有分时走势明细。
  const flatSectorPoints = useMemo(
    () => (sectorReturn != null ? buildFlatIntradayPoints(sectorReturn) : null),
    [sectorReturn],
  );
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
  const intradayFallbackQuery = useMemo(
    () => resolveIntradayFallbackQuery(activeHolding, intradayQuery),
    [activeHolding, intradayQuery],
  );
  const intradayQueryKey = intradayQuery
    ? `${intradayQuery.source_type}:${intradayQuery.source_name}`
    : "";
  const intradayQueryRef = useRef(intradayQuery);
  const intradayFallbackQueryRef = useRef(intradayFallbackQuery);

  const navHoldings = useMemo(() => navigableHoldings(holdings), [holdings]);
  const navIndex = useMemo(() => {
    const idx = findHoldingIndex(navHoldings, holding);
    return idx >= 0 ? idx : Math.max(0, Math.min(holdingIndex, navHoldings.length - 1));
  }, [navHoldings, holding, holdingIndex]);
  const canWrapNavigate = navHoldings.length > 1;

  function navigateRelative(offset: number) {
    if (!canWrapNavigate) {
      return;
    }
    const nextIndex = (navIndex + offset + navHoldings.length) % navHoldings.length;
    const target = navHoldings[nextIndex];
    if (target) {
      onNavigate({
        fund_code: target.fund_code,
        fund_name: target.fund_name,
      });
    }
  }

  const canGoPrev = canWrapNavigate;
  const canGoNext = canWrapNavigate;

  useEffect(() => {
    activeHoldingRef.current = activeHolding;
    onHoldingResolvedRef.current = onHoldingResolved;
    detailInputsRef.current = {
      holdings,
      portfolioSummary,
      sectorMeta,
      onHoldingResolved,
    };
    detailTargetRef.current = selectedHolding ?? holding;
    intradayQueryRef.current = intradayQuery;
    intradayFallbackQueryRef.current = intradayFallbackQuery;
  }, [
    activeHolding,
    holding,
    holdings,
    intradayFallbackQuery,
    intradayQuery,
    onHoldingResolved,
    portfolioSummary,
    selectedHolding,
    sectorMeta,
  ]);

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
      writeHoldingDetailCache(userId, result.holding.fund_code, result);
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
      writeHoldingDetailCache(userId, result.holding.fund_code, result);
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

  async function refreshDetailAfterPortfolioMutation(
    mutationResult: HoldingMutationResult | null,
  ): Promise<void> {
    if (!mutationResult) {
      return;
    }
    const nextIndex = findHoldingIndex(mutationResult.holdings, activeHolding);
    if (nextIndex < 0) {
      setDetailError("持仓已更新，但暂时无法定位该基金的最新详情。");
      return;
    }

    try {
      setDetailError(null);
      const result = await fetchHoldingDetail({
        holdings: mutationResult.holdings,
        index: nextIndex,
        portfolio_summary: mutationResult.portfolioSummary ?? portfolioSummary,
        sector_quote_meta: sectorMeta,
      });
      writeHoldingDetailCache(userId, result.holding.fund_code, result);
      setDetail(result);
    } catch (error) {
      const message = error instanceof Error ? error.message : "详情刷新失败";
      setDetailError(`持仓已更新，但最新详情暂时无法刷新：${message}`);
    }
  }

  useEffect(() => {
    let cancelled = false;
    const inputs = detailInputsRef.current;
    const detailTarget = detailTargetRef.current;
    const fundCode = detailTarget?.fund_code;
    const resolvedIndex = findHoldingIndex(inputs.holdings, detailTarget);
    const detailIndex = resolvedIndex >= 0 ? resolvedIndex : holdingIndex;
    const cachedDetail = readHoldingDetailCache(userId, fundCode);

    if (cachedDetail) {
      setDetail(cachedDetail);
      setDetailLoading(false);
    } else {
      setDetailLoading(true);
    }
    setDetailError(null);

    // 缓存期内也静默后台更新（stale-while-revalidate）
    void fetchHoldingDetail({
      holdings: inputs.holdings,
      index: detailIndex,
      portfolio_summary: inputs.portfolioSummary,
      sector_quote_meta: inputs.sectorMeta,
    })
      .then((result) => {
        if (cancelled) {
          return;
        }
        if (result.holding.fund_code) {
          writeHoldingDetailCache(userId, result.holding.fund_code, result);
        }
        setDetail(result);
        const latestInputs = detailInputsRef.current;
        const latestIndex = findHoldingIndex(latestInputs.holdings, detailTarget);
        const applyIndex = latestIndex >= 0 ? latestIndex : detailIndex;
        if (
          result.fund_code_resolved &&
          result.holding.fund_code !== latestInputs.holdings[applyIndex]?.fund_code
        ) {
          latestInputs.onHoldingResolved?.(applyIndex, result.holding);
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
  }, [detailRequestKey, holdingIndex, userId]);

  useEffect(() => {
    if (tab !== "sector") {
      return;
    }
    const query = intradayQueryRef.current;
    if (!query) {
      setIntradayPoints([]);
      setIntradayClosePercent(null);
      setIntradayNote("暂无板块映射，请先在持仓页刷新板块或上传详情截图建档");
      setIntradayLoading(false);
      setIntradayRefreshing(false);
      return;
    }

    const requestId = ++intradayRequestSeq.current;
    const forceRefresh = intradayForceSeq > 0;
    const cachedIntraday = !forceRefresh ? readIntradayCache(query) : null;

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
      const currentHolding = activeHoldingRef.current;
      const merged = mergeSectorIntradayClose(currentHolding, close);
      if (merged !== currentHolding) {
        onHoldingResolvedRef.current?.(
          findHoldingIndex(detailInputsRef.current.holdings, currentHolding),
          merged,
        );
      }
    };

    if (cachedIntraday && cachedIntraday.points.length >= 2) {
      applyIntraday(cachedIntraday);
      setIntradayLoading(false);
    } else if (cachedIntraday) {
      if (cachedIntraday.note) {
        setIntradayNote(cachedIntraday.note);
      }
      setIntradayLoading(false);
    } else {
      setIntradayPoints([]);
      setIntradayClosePercent(null);
      setIntradayNote(null);
      setIntradayLoading(true);
    }
    // 缓存命中时不显示刷新动画；手动 forceRefresh 才亮 spinner
    setIntradayRefreshing(forceRefresh);

    void (async () => {
      try {
        let result = await fetchSectorIntraday(
          query,
          forceRefresh ? { forceRefresh: true } : undefined,
        );
        if (requestId !== intradayRequestSeq.current) {
          return;
        }
        // 主查询（常见于业绩基准原文抠出的场内指数名）查不到数据时，退回按"关联板块"
        // 短名再试一次——短名大多已经注册过行情源，不必强行扩充指数名别名表。
        const fallbackQuery = intradayFallbackQueryRef.current;
        if (result.points.length < 2 && fallbackQuery) {
          try {
            const fallbackResult = await fetchSectorIntraday(
              fallbackQuery,
              forceRefresh ? { forceRefresh: true } : undefined,
            );
            if (requestId !== intradayRequestSeq.current) {
              return;
            }
            if (fallbackResult.points.length >= 2) {
              result = fallbackResult;
            }
          } catch {
            // 兜底查询失败时保留主查询结果（含其 note），静默忽略。
          }
        }
        writeIntradayCache(query, result);
        if (result.points.length >= 2 || !cachedIntraday) {
          applyIntraday(result);
        } else if (result.note && !cachedIntraday.note) {
          setIntradayNote(result.note);
        }
      } catch {
        if (requestId !== intradayRequestSeq.current) {
          return;
        }
        if (!cachedIntraday) {
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
  }, [tab, intradayQueryKey, intradayForceSeq, holdingIndex]);

  useEffect(() => {
    return hydrateTradingSession((session) => {
      setQuoteTradeDate(formatTradeDateShort(session.effective_trade_date));
    });
  }, []);

  const tradeDateLabel = quoteTradeDate ?? "—";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-end justify-center bg-slate-950/50 p-0 sm:items-center sm:p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        ref={detailDialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fund-detail-title"
        className="flex max-h-[min(100dvh,920px)] w-full max-w-lg flex-col overflow-y-auto overscroll-contain bg-white shadow-2xl sm:max-h-[min(92dvh,860px)] sm:rounded-2xl"
      >
        <div className="sticky top-0 z-10 shrink-0">
          <header
            className="px-3 pb-3 pt-3 text-white shadow-sm"
            style={{ background: "linear-gradient(135deg, var(--brand) 0%, var(--brand-strong) 100%)" }}
          >
            <div className="flex items-center justify-between gap-2">
              <button
                ref={mainCloseButtonRef}
                type="button"
                onClick={onClose}
                className="inline-flex h-11 w-11 items-center justify-center rounded-full hover:bg-white/15"
                aria-label="返回"
              >
                <ChevronLeft size={20} />
              </button>
              <div className="min-w-0 flex-1 text-center">
                <h2 id="fund-detail-title" className="truncate text-sm font-bold leading-tight">
                  {activeHolding.fund_name}
                </h2>
                <div className="text-[10px] text-white/80">
                  <button
                    type="button"
                    onClick={() => {
                      setFundCodeError(null);
                      setFundCodeEditOpen(true);
                    }}
                    className={`inline-flex min-h-11 items-center gap-1 rounded-full px-2 py-1 transition hover:bg-white/15 ${
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
                  onClick={() => navigateRelative(-1)}
                  className="inline-flex h-11 w-11 items-center justify-center rounded-full hover:bg-white/15 disabled:opacity-30"
                  aria-label="上一只"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  type="button"
                  disabled={!canGoNext}
                  onClick={() => navigateRelative(1)}
                  className="inline-flex h-11 w-11 items-center justify-center rounded-full hover:bg-white/15 disabled:opacity-30"
                  aria-label="下一只"
                >
                  <ChevronRight size={16} />
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="inline-flex h-11 w-11 items-center justify-center rounded-full hover:bg-white/15"
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
          <div role="alert" className="border-b border-rose-100 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">
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
            className="flex min-h-11 w-full items-center justify-center text-slate-500 transition hover:text-slate-700"
            aria-label={holdingsExpanded ? "收起持仓明细" : "展开持仓明细"}
          >
            {holdingsExpanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
        </div>

        <div className="border-b border-slate-100 bg-white px-4">
          <div className="flex gap-6 text-[15px] font-bold" role="tablist" aria-label="基金详情视图">
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
                id={`fund-detail-tab-${id}`}
                role="tab"
                aria-selected={tab === id}
                aria-controls={`fund-detail-panel-${id}`}
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

        <div
          id={`fund-detail-panel-${tab}`}
          role="tabpanel"
          aria-labelledby={`fund-detail-tab-${tab}`}
          className="px-3 py-3"
        >
          {tab === "sector" ? (
            <div>
              <div className="mb-1 flex items-center gap-2 border-b border-slate-100 pb-2 text-xs">
                <span className="shrink-0 text-slate-500">日期 {tradeDateLabel}</span>
                <span className="flex min-w-0 flex-1 items-center justify-center gap-0.5 truncate font-bold text-slate-800">
                  {quoteLabel}
                  <ChevronDown size={12} className="shrink-0 text-slate-500" />
                </span>
                <span
                  className={`shrink-0 text-sm font-black tabular-nums ${cnProfitClass(displaySectorReturn)}`}
                >
                  {formatSignedPercent(displaySectorReturn)}
                </span>
                <span className="flex shrink-0 items-center gap-1 text-[10px] text-slate-500">
                  {dataSourceLabel}
                  <button
                    type="button"
                    onClick={() => setIntradayForceSeq((value) => value + 1)}
                    disabled={intradayRefreshing || intradayLoading}
                    className="inline-flex h-11 w-11 items-center justify-center rounded-xl hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
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
                <div className="flex h-[200px] items-center justify-center text-sm text-slate-500">
                  <Loader2 size={18} className="mr-2 animate-spin" />
                  加载分时…
                </div>
              ) : intradayPoints.length >= 2 ? (
                <IntradayPercentChart points={intradayPoints} height={200} />
              ) : flatSectorPoints ? (
                <div>
                  <IntradayPercentChart points={flatSectorPoints} height={200} flat />
                  <div className="mt-1 text-center text-[11px] text-slate-500">
                    {intradayNote ?? "暂无分时明细，以下按当日板块涨跌绘制水平线"}
                  </div>
                </div>
              ) : (
                <div className="flex h-[200px] flex-col items-center justify-center gap-1 px-4 text-center text-sm text-slate-500">
                  <span>{intradayNote ?? "暂无分时数据"}</span>
                </div>
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
                <p className="pb-1 text-center text-[11px] text-slate-500">{intradayNote}</p>
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
              className="flex min-h-11 flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
            >
              <ChevronLeft size={18} className="text-slate-500" />
              返回列表
            </button>
            <button
              type="button"
              onClick={() => setModifyOpen(true)}
              className="flex min-h-11 flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-[#2356e0] hover:bg-blue-50"
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
                className="flex min-h-11 flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-rose-600 hover:bg-rose-50"
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
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) {
                setDeleteConfirmOpen(false);
              }
            }}
            role="presentation"
          >
            <div
              ref={deleteDialogRef}
              tabIndex={-1}
              className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl"
              role="dialog"
              aria-modal="true"
              aria-labelledby="delete-holding-title"
            >
              <h3 id="delete-holding-title" className="text-base font-bold text-slate-900">
                删除该基金？
              </h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                将从当前账户汇总移除「{activeHolding.fund_name}」，并删除该基金档案。重新添加时将作为新持仓录入。
              </p>
              <div className="mt-4 flex gap-2">
                <button
                  ref={deleteCancelButtonRef}
                  type="button"
                  onClick={() => setDeleteConfirmOpen(false)}
                  className="btn-secondary min-h-11 flex-1 !py-2.5"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={handleDeleteHolding}
                  className="min-h-11 flex-1 rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-rose-700"
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
          onSubmit={async (patch) => {
            if (!onAdjustHolding) {
              throw new Error("当前无法修改持仓，请稍后重试。");
            }
            const mutationResult = await onAdjustHolding(activeHolding.fund_code, patch);
            await refreshDetailAfterPortfolioMutation(mutationResult);
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
          onSubmit={async (transaction) => {
            if (!onApplyTransaction) {
              throw new Error("当前无法同步交易，请稍后重试。");
            }
            const mutationResult = await onApplyTransaction(transaction);
            await refreshDetailAfterPortfolioMutation(mutationResult);
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
        {hint ? <div className="truncate text-[10px] text-slate-500">{hint}</div> : null}
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
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: open && draftDate != null,
    onClose,
    initialFocusRef: closeButtonRef,
  });

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
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="w-full max-w-sm rounded-t-3xl bg-white p-5 shadow-2xl sm:rounded-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="purchase-date-title"
        aria-busy={saving}
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
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 text-center">
          <div className="text-[11px] text-slate-500">
            {previewDays != null ? "预计持有天数" : "当前持有天数"}
          </div>
          <div className="mt-1 text-2xl font-black tabular-nums text-slate-900">
            {(previewDays ?? holdingDays) != null ? `${previewDays ?? holdingDays} 天` : "—"}
          </div>
          {hint && !previewDays ? <div className="mt-1 text-[10px] text-slate-500">{hint}</div> : null}
        </div>

        <div className="mt-4">
          <WheelDatePicker
            key={pickerSession}
            value={draftDate}
            max={todayIsoDate()}
            onChange={setDraftDate}
          />
        </div>

        {error ? (
          <p className="mt-2 text-xs font-medium text-rose-700" role="alert">
            {error}
          </p>
        ) : null}

        <div className="mt-4 flex gap-2">
          {firstPurchaseDate ? (
            <button
              type="button"
              disabled={saving}
              onClick={() => onDateChange("")}
              className="min-h-11 flex-1 rounded-xl border border-slate-200 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              清除日期
            </button>
          ) : null}
          <button
            type="button"
            disabled={saving || !draftDate}
            onClick={() => onDateChange(draftDate)}
            className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-[var(--brand)] py-2.5 text-sm font-semibold text-white hover:bg-[var(--brand-strong)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : null}
            完成
          </button>
        </div>
      </div>
    </div>
  );
}
