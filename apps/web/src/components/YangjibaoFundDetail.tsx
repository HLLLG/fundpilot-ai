"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Edit3,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import type { Holding, HoldingDetail, PortfolioSummary, SectorQuoteMeta } from "@/lib/api";
import {
  fetchFundNavHistory,
  fetchHoldingDetail,
  fetchSectorIntraday,
  fetchTradingSession,
} from "@/lib/api";
import { IntradayPercentChart } from "@/components/IntradayPercentChart";
import { NavLineChart } from "@/components/NavLineChart";
import {
  cnProfitClass,
  computeCostBasis,
  computeDailyProfit,
  computeEstimatedDailyReturnPercent,
  computeEstimatedHoldingReturnPercent,
  computeHoldingProfit,
  computeHoldingWeight,
  formatPlainMoney,
  formatPlainPercent,
  formatSignedMoney,
  formatSignedPercent,
  resolveSectorBoardReturnPercent,
} from "@/lib/holdingMetrics";
import { resolveIntradayQuery, sectorQuoteLookupLabel } from "@/lib/profileSector";
import { isEstimateFallbackMeta } from "@/lib/sectorQuoteStatus";
import { formatTradeDateShort } from "@/lib/tradeDateLabel";

type DetailTab = "sector" | "performance" | "profit";

const PROVENANCE_LABEL: Record<string, string> = {
  ocr_detail: "详情 OCR",
  nav: "净值推算",
  snapshot: "历史快照",
  computed: "公式估算",
  profile: "基金档案",
  akshare: "AkShare 匹配",
};

type YangjibaoFundDetailProps = {
  holding: Holding;
  holdingIndex: number;
  holdings: Holding[];
  portfolioSummary?: PortfolioSummary | null;
  sectorMeta?: SectorQuoteMeta;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onEdit?: () => void;
  onHoldingResolved?: (index: number, holding: Holding) => void;
};

function HeaderStat({
  label,
  value,
  valueClass = "text-white",
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="px-2 py-2 text-center">
      <div className="text-[11px] text-white/75">{label}</div>
      <div className={`mt-1 text-lg font-black tabular-nums leading-none ${valueClass}`}>{value}</div>
    </div>
  );
}

function GridStat({
  label,
  value,
  valueClass = "text-slate-900",
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="px-2 py-3 text-center">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className={`mt-1.5 text-[15px] font-black tabular-nums leading-tight ${valueClass}`}>
        {value}
      </div>
    </div>
  );
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
  onEdit,
  onHoldingResolved,
}: YangjibaoFundDetailProps) {
  const [tab, setTab] = useState<DetailTab>("sector");
  const [detail, setDetail] = useState<HoldingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [navLoading, setNavLoading] = useState(false);
  const [navPoints, setNavPoints] = useState<Array<{ date: string; nav: number }>>([]);
  const [navPeriodChange, setNavPeriodChange] = useState<number | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  const [intradayPoints, setIntradayPoints] = useState<Array<{ time: string; percent: number }>>([]);
  const [intradayClosePercent, setIntradayClosePercent] = useState<number | null>(null);
  const [intradayNote, setIntradayNote] = useState<string | null>(null);
  const [intradayRefreshing, setIntradayRefreshing] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [holdingsExpanded, setHoldingsExpanded] = useState(true);
  const [quoteTradeDate, setQuoteTradeDate] = useState<string | null>(null);
  const intradayRequestSeq = useRef(0);

  const activeHolding = detail?.holding ?? holding;
  const provenance = detail?.provenance ?? {};

  const totalAssets =
    portfolioSummary?.total_assets ??
    (holdings.reduce((sum, item) => sum + item.holding_amount, 0) || null);

  const holdingReturn = computeEstimatedHoldingReturnPercent(activeHolding);
  const holdingProfit = computeHoldingProfit(activeHolding);
  const dailyProfit = computeDailyProfit(activeHolding);
  const costBasis = computeCostBasis(activeHolding);
  const weight = computeHoldingWeight(activeHolding, totalAssets);

  const shares = detail?.holding_shares ?? null;
  const unitCost = detail?.holding_cost ?? null;
  const yesterdayProfit = detail?.yesterday_profit ?? null;
  const holdingDays = detail?.holding_days ?? null;
  const latestNav = detail?.latest_nav ?? null;
  const yearReturn = detail?.year_return_percent ?? null;

  const quoteLabel = sectorQuoteLookupLabel(activeHolding) ?? sectorMeta?.matched_name ?? "—";
  const sectorReturn = resolveSectorBoardReturnPercent(activeHolding);
  const emKlineClose =
    intradayClosePercent ??
    (intradayPoints.length > 0 ? intradayPoints[intradayPoints.length - 1].percent : null);
  const displaySectorReturn = emKlineClose ?? sectorReturn;
  const dataSourceLabel = isEstimateFallbackMeta(sectorMeta)
    ? "估值兜底"
    : sectorMeta?.provider === "eastmoney-kline" || sectorMeta?.source === "live"
      ? "东财"
      : "数据源";
  const displayDailyReturn =
    activeHolding.daily_return_percent ?? computeEstimatedDailyReturnPercent(activeHolding);

  const intradayQuery = useMemo(
    () => resolveIntradayQuery(activeHolding, sectorMeta),
    [activeHolding, sectorMeta],
  );

  const canGoPrev = holdingIndex > 0;
  const canGoNext = holdingIndex < holdings.length - 1;

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
    setDetailLoading(true);
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
        setDetail(result);
        if (
          result.fund_code_resolved &&
          result.holding.fund_code !== holdings[holdingIndex]?.fund_code
        ) {
          onHoldingResolved?.(holdingIndex, result.holding);
        }
      })
      .catch((error) => {
        if (!cancelled) {
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
    if (tab !== "performance" || !detail?.fund_code_resolved) {
      return;
    }
    let cancelled = false;
    setNavLoading(true);
    void fetchFundNavHistory(activeHolding.fund_code, 252)
      .then((history) => {
        if (cancelled) {
          return;
        }
        setNavPoints(history.points.map((point) => ({ date: point.date, nav: point.nav })));
        setNavPeriodChange(history.period_change_percent ?? null);
      })
      .catch(() => {
        if (!cancelled) {
          setNavPoints([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setNavLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tab, activeHolding.fund_code, detail?.fund_code_resolved]);

  useEffect(() => {
    if (tab !== "sector") {
      return;
    }
    if (!intradayQuery) {
      setIntradayPoints([]);
      setIntradayClosePercent(null);
      setIntradayNote("暂无板块映射，请先在「今日」刷新板块或上传详情截图建档");
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
      close_change_percent?: number | null;
      note?: string | null;
    }) => {
      setIntradayPoints(result.points);
      setIntradayClosePercent(result.close_change_percent ?? null);
      setIntradayNote(result.note ?? null);
    };

    void (async () => {
      let showedCache = false;

      try {
        const cached = await fetchSectorIntraday(intradayQuery);
        if (requestId !== intradayRequestSeq.current) {
          return;
        }
        if (cached.points.length >= 2) {
          applyIntraday(cached);
          showedCache = true;
          setIntradayLoading(false);
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
          setIntradayClosePercent(null);
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
  }, [tab, intradayQuery]);

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
        <header className="sticky top-0 z-10 shrink-0 bg-[#3d7eff] px-3 pb-2.5 pt-3 text-white shadow-sm">
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
                {activeHolding.fund_code}
                {detail?.fund_code_source === "akshare" ? " · 已自动匹配代码" : ""}
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

          <div className="mt-3 grid grid-cols-3 divide-x divide-white/25">
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
        </header>

        {detailError ? (
          <div className="border-b border-rose-100 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">
            {detailError}
          </div>
        ) : null}

        <div className="border-b border-slate-100 bg-white">
          {holdingsExpanded ? (
            <div className="divide-y divide-slate-100">
              <div className="grid grid-cols-3 divide-x divide-slate-100">
                <GridStat label="持有金额" value={formatPlainMoney(activeHolding.holding_amount)} />
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
                    ? "border-[#3d7eff] text-[#3d7eff]"
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
                <span className="flex shrink-0 items-center gap-0.5 text-[10px] text-slate-400">
                  {dataSourceLabel}
                  {intradayRefreshing ? (
                    <Loader2 size={10} className="animate-spin" />
                  ) : (
                    <RefreshCw size={10} />
                  )}
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
            <div>
              {navLoading ? (
                <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">
                  <Loader2 size={18} className="mr-2 animate-spin" />
                  加载净值走势…
                </div>
              ) : navPoints.length >= 2 ? (
                <NavLineChart
                  points={navPoints}
                  periodChangePercent={navPeriodChange ?? yearReturn}
                  height={200}
                />
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-400">
                  {!detail?.fund_code_resolved
                    ? "正在匹配基金代码，或请上传详情页 OCR 补全"
                    : "暂无净值历史数据"}
                </div>
              )}
              {latestNav != null ? (
                <p className="mt-1.5 text-center text-[11px] text-slate-400">
                  最新净值 {latestNav.toFixed(4)}
                  {detail?.nav_date ? ` · ${detail.nav_date}` : ""}
                </p>
              ) : null}
            </div>
          ) : null}

          {tab === "profit" ? (
            <div className="space-y-2">
              <ProfitRow label="持有金额" value={`¥ ${formatPlainMoney(activeHolding.holding_amount)}`} />
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

        <footer className="sticky bottom-0 z-10 grid shrink-0 grid-cols-2 divide-x divide-slate-100 border-t border-slate-100 bg-white">
          <button
            type="button"
            onClick={onEdit}
            className="flex flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
          >
            <Edit3 size={18} className="text-slate-500" />
            修改持仓
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex flex-col items-center justify-center gap-1 py-3 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
          >
            <ChevronLeft size={18} className="text-slate-500" />
            返回列表
          </button>
        </footer>
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
