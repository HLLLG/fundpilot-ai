"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Edit3,
  Loader2,
  X,
} from "lucide-react";
import type { Holding, HoldingDetail, PortfolioSummary, SectorQuoteMeta } from "@/lib/api";
import { fetchFundNavHistory, fetchHoldingDetail, fetchSectorIntraday } from "@/lib/api";
import { IntradayPercentChart } from "@/components/IntradayPercentChart";
import { NavLineChart } from "@/components/NavLineChart";
import {
  cnProfitClass,
  computeCostBasis,
  computeDailyProfit,
  computeEstimatedDailyReturnPercent,
  computeHoldingProfit,
  computeHoldingWeight,
  dailyProfitIsEstimated,
  formatPlainMoney,
  formatPlainPercent,
  formatSignedMoney,
  formatSignedPercent,
  holdingProfitIsEstimated,
  resolveHoldingReturnPercent,
} from "@/lib/holdingMetrics";

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

function StatCell({
  label,
  value,
  valueClass = "text-slate-900",
  sub,
}: {
  label: string;
  value: string;
  valueClass?: string;
  sub?: string;
}) {
  return (
    <div className="px-3 py-2.5 text-center">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className={`mt-1 text-[15px] font-black tabular-nums ${valueClass}`}>{value}</div>
      {sub ? <div className="mt-0.5 text-[10px] text-slate-400">{sub}</div> : null}
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
  const [intradayNote, setIntradayNote] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const activeHolding = detail?.holding ?? holding;
  const provenance = detail?.provenance ?? {};

  const totalAssets =
    portfolioSummary?.total_assets ??
    (holdings.reduce((sum, item) => sum + item.holding_amount, 0) || null);

  const holdingReturn = resolveHoldingReturnPercent(activeHolding);
  const holdingProfit = computeHoldingProfit(activeHolding);
  const dailyProfit = computeDailyProfit(activeHolding);
  const dailyReturn =
    activeHolding.daily_return_percent ?? computeEstimatedDailyReturnPercent(activeHolding);
  const costBasis = computeCostBasis(activeHolding);
  const weight = computeHoldingWeight(activeHolding, totalAssets);

  const shares = detail?.holding_shares ?? null;
  const unitCost = detail?.holding_cost ?? null;
  const yesterdayProfit = detail?.yesterday_profit ?? null;
  const holdingDays = detail?.holding_days ?? null;
  const latestNav = detail?.latest_nav ?? null;
  const yearReturn = detail?.year_return_percent ?? null;

  const sectorBoardLabel =
    activeHolding.sector_name ?? activeHolding.intraday_index_name ?? "—";
  const sectorQuoteLabel =
    activeHolding.intraday_index_name ?? activeHolding.sector_name ?? "—";
  const sectorName = sectorMeta?.matched_name ?? sectorQuoteLabel;
  const sectorReturn = activeHolding.sector_return_percent;

  const canGoPrev = holdingIndex > 0;
  const canGoNext = holdingIndex < holdings.length - 1;

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
    if (tab !== "sector" || !sectorMeta?.source_type || !sectorMeta.matched_name) {
      return;
    }
    let cancelled = false;
    setIntradayLoading(true);
    void fetchSectorIntraday({
      source_type: sectorMeta.source_type,
      source_name: sectorMeta.matched_name,
    })
      .then((result) => {
        if (cancelled) {
          return;
        }
        setIntradayPoints(result.points);
        setIntradayNote(result.note ?? null);
      })
      .catch(() => {
        if (!cancelled) {
          setIntradayPoints([]);
          setIntradayNote("分时数据暂不可用");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIntradayLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tab, sectorMeta?.matched_name, sectorMeta?.source_type]);

  const todayLabel = useMemo(() => {
    const now = new Date();
    return `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  }, []);

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-slate-950/50 p-0 sm:items-center sm:p-4">
      <div className="flex h-[100dvh] w-full max-w-lg flex-col overflow-hidden bg-white shadow-2xl sm:h-[min(92dvh,860px)] sm:rounded-2xl">
        <header className="shrink-0 bg-[#3d7eff] px-3 pb-3 pt-4 text-white">
          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-9 w-9 items-center justify-center rounded-full hover:bg-white/15"
              aria-label="返回"
            >
              <ChevronLeft size={22} />
            </button>
            <div className="min-w-0 flex-1 text-center">
              <div className="truncate text-[15px] font-bold leading-tight">{activeHolding.fund_name}</div>
              <div className="text-[11px] text-white/80">
                {activeHolding.fund_code}
                {detail?.fund_code_source === "akshare" ? " · 已自动匹配代码" : ""}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={!canGoPrev}
                onClick={() => onNavigate(holdingIndex - 1)}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full hover:bg-white/15 disabled:opacity-30"
                aria-label="上一只"
              >
                <ChevronLeft size={18} />
              </button>
              <button
                type="button"
                disabled={!canGoNext}
                onClick={() => onNavigate(holdingIndex + 1)}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full hover:bg-white/15 disabled:opacity-30"
                aria-label="下一只"
              >
                <ChevronRight size={18} />
              </button>
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full hover:bg-white/15 sm:hidden"
                aria-label="关闭"
              >
                <X size={18} />
              </button>
            </div>
          </div>

          <div className="mt-3 grid grid-cols-3 divide-x divide-white/20 rounded-xl bg-white/10">
            <StatCell
              label="当日涨幅"
              value={formatSignedPercent(dailyReturn)}
              valueClass={cnProfitClass(dailyReturn)}
            />
            <StatCell
              label="近1年"
              value={
                yearReturn != null
                  ? formatSignedPercent(yearReturn)
                  : detailLoading
                    ? "…"
                    : "—"
              }
              valueClass={cnProfitClass(yearReturn)}
            />
            <StatCell
              label="板块实时"
              value={formatSignedPercent(sectorReturn)}
              valueClass={cnProfitClass(sectorReturn)}
              sub={sectorMeta?.source === "live" ? "东财" : undefined}
            />
          </div>
        </header>

        {detailError ? (
          <div className="shrink-0 border-b border-rose-100 bg-rose-50 px-4 py-2.5 text-xs font-semibold text-rose-700">
            {detailError}
          </div>
        ) : null}

        <div className="shrink-0 border-b border-slate-100 bg-white">
          <div className="grid grid-cols-3 divide-x divide-slate-100 border-b border-slate-100">
            <StatCell label="持有金额" value={formatPlainMoney(activeHolding.holding_amount)} />
            <StatCell
              label="持有份额"
              value={shares != null ? formatPlainMoney(shares) : detailLoading ? "…" : "—"}
              sub={sourceHint(provenance, "holding_shares")}
            />
            <StatCell label="持仓占比" value={weight != null ? formatPlainPercent(weight) : "—"} />
          </div>
          <div className="grid grid-cols-3 divide-x divide-slate-100 border-b border-slate-100">
            <StatCell
              label="持有收益"
              value={`${holdingProfitIsEstimated(activeHolding) ? "≈" : ""}${formatSignedMoney(holdingProfit)}`}
              valueClass={cnProfitClass(holdingProfit)}
            />
            <StatCell
              label="持有收益率"
              value={formatSignedPercent(holdingReturn)}
              valueClass={cnProfitClass(holdingReturn)}
            />
            <StatCell
              label="持仓成本"
              value={unitCost != null ? unitCost.toFixed(4) : detailLoading ? "…" : "—"}
              sub={sourceHint(provenance, "holding_cost")}
            />
          </div>
          <div className="grid grid-cols-3 divide-x divide-slate-100">
            <StatCell
              label="当日收益"
              value={`${dailyProfitIsEstimated(activeHolding) ? "≈" : ""}${formatSignedMoney(dailyProfit)}`}
              valueClass={cnProfitClass(dailyProfit)}
            />
            <StatCell
              label="昨日收益"
              value={yesterdayProfit != null ? formatSignedMoney(yesterdayProfit) : detailLoading ? "…" : "—"}
              valueClass={cnProfitClass(yesterdayProfit)}
              sub={sourceHint(provenance, "yesterday_profit")}
            />
            <StatCell
              label="持有天数"
              value={holdingDays != null ? `${holdingDays} 天` : detailLoading ? "…" : "—"}
              sub={sourceHint(provenance, "holding_days")}
            />
          </div>
        </div>

        <div className="shrink-0 border-b border-slate-100 px-4">
          <div className="flex gap-6 text-sm font-bold">
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
                className={`border-b-2 py-3 transition ${
                  tab === id
                    ? "border-[#3d7eff] text-[#3d7eff]"
                    : "border-transparent text-slate-400"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {tab === "sector" ? (
            <div>
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="text-sm font-bold text-slate-700">
                  {todayLabel} · {sectorName}
                </div>
                <div className={`text-lg font-black tabular-nums ${cnProfitClass(sectorReturn)}`}>
                  {formatSignedPercent(sectorReturn)}
                </div>
              </div>
              {intradayLoading ? (
                <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">
                  <Loader2 size={18} className="mr-2 animate-spin" />
                  加载分时…
                </div>
              ) : (
                <IntradayPercentChart points={intradayPoints} />
              )}
              {activeHolding.intraday_index_name &&
              activeHolding.sector_name &&
              activeHolding.intraday_index_name !== activeHolding.sector_name ? (
                <p className="mt-2 text-center text-xs text-slate-400">
                  关联板块 {sectorBoardLabel} · 涨跌按场内指数 {activeHolding.intraday_index_name}
                </p>
              ) : null}
              {intradayNote ? (
                <p className="mt-2 text-center text-xs text-slate-400">{intradayNote}</p>
              ) : null}
            </div>
          ) : null}

          {tab === "performance" ? (
            <div>
              {navLoading ? (
                <div className="flex h-[240px] items-center justify-center text-sm text-slate-400">
                  <Loader2 size={18} className="mr-2 animate-spin" />
                  加载净值走势…
                </div>
              ) : navPoints.length >= 2 ? (
                <NavLineChart points={navPoints} periodChangePercent={navPeriodChange ?? yearReturn} height={240} />
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-400">
                  {!detail?.fund_code_resolved
                    ? "正在匹配基金代码，或请上传详情页 OCR 补全"
                    : "暂无净值历史数据"}
                </div>
              )}
              {latestNav != null ? (
                <p className="mt-2 text-center text-xs text-slate-400">
                  最新净值 {latestNav.toFixed(4)}
                  {detail?.nav_date ? ` · ${detail.nav_date}` : ""}
                </p>
              ) : null}
            </div>
          ) : null}

          {tab === "profit" ? (
            <div className="space-y-3">
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
                hint={holdingProfitIsEstimated(activeHolding) ? "由持有收益率估算" : undefined}
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
              {weight != null ? (
                <ProfitRow label="占账户比例" value={formatPlainPercent(weight)} />
              ) : null}
            </div>
          ) : null}
        </div>

        <footer className="grid shrink-0 grid-cols-2 gap-px border-t border-slate-100 bg-slate-100">
          <button
            type="button"
            onClick={onEdit}
            className="flex items-center justify-center gap-2 bg-white py-3.5 text-sm font-bold text-slate-700 hover:bg-slate-50"
          >
            <Edit3 size={16} />
            修改持仓
          </button>
          <button
            type="button"
            onClick={onClose}
            className="bg-white py-3.5 text-sm font-bold text-slate-700 hover:bg-slate-50"
          >
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
    <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50/80 px-4 py-3">
      <div>
        <div className="text-xs text-slate-500">{label}</div>
        {hint ? <div className="mt-0.5 text-[10px] text-slate-400">{hint}</div> : null}
      </div>
      <div className={`text-sm font-black tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}
