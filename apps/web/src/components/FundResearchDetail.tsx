"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  Layers3,
  LineChart,
  Loader2,
  RefreshCw,
  WalletCards,
  X,
} from "lucide-react";
import { IntradayPercentChart } from "@/components/IntradayPercentChart";
import { PerformanceTrendPanel } from "@/components/PerformanceTrendPanel";
import {
  fetchFundPublicOverview,
  fetchSectorIntraday,
  type FundHoldingSummary,
  type FundPublicOverview,
  type FundSearchItem,
  type Holding,
  type SectorIntradayResult,
} from "@/lib/api";
import { useDialogA11y } from "@/lib/useDialogA11y";

type DetailTab = "overview" | "relation" | "performance" | "holding";

type FundResearchDetailProps = {
  fund: FundSearchItem;
  holding?: Holding | null;
  onClose: () => void;
};

function signedPercent(value: number | null | undefined, approximate = false) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }
  return `${approximate ? "≈" : ""}${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function percentTone(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return "text-slate-500";
  }
  if (value > 0) {
    return "text-rose-600";
  }
  if (value < 0) {
    return "text-emerald-600";
  }
  return "text-slate-700";
}

function currency(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2,
  }).format(value);
}

function holdingSummary(
  propHolding: Holding | null | undefined,
  overview: FundPublicOverview | null,
): FundHoldingSummary | null {
  if (propHolding) {
    return propHolding;
  }
  return overview?.holding ?? null;
}

export function FundResearchDetail({ fund, holding, onClose }: FundResearchDetailProps) {
  const [overview, setOverview] = useState<FundPublicOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const [intraday, setIntraday] = useState<SectorIntradayResult | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose,
    initialFocusRef: closeButtonRef,
  });

  const loadOverview = () => {
    setLoading(true);
    setError(null);
    setOverview(null);
    void fetchFundPublicOverview(fund.fund_code)
      .then(setOverview)
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : "基金详情加载失败");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setActiveTab("overview");
    setIntraday(null);
    loadOverview();
    // fund_code is the request identity; fund_name is display-only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fund.fund_code]);

  useEffect(() => {
    const relation = overview?.relation;
    if (
      !relation?.price_proxy_eligible ||
      !relation.source_type ||
      !relation.source_name ||
      !["index", "concept", "industry"].includes(relation.source_type)
    ) {
      setIntraday(null);
      return;
    }
    let cancelled = false;
    setIntradayLoading(true);
    void fetchSectorIntraday({
      source_type: relation.source_type,
      source_name: relation.source_name,
    })
      .then((result) => {
        if (!cancelled) setIntraday(result);
      })
      .catch(() => {
        if (!cancelled) setIntraday(null);
      })
      .finally(() => {
        if (!cancelled) setIntradayLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [overview?.relation]);

  const held = holdingSummary(holding, overview);
  const tabs = useMemo(
    () => [
      { id: "overview" as const, label: "基金概览", icon: BarChart3 },
      { id: "relation" as const, label: "关联板块", icon: Layers3 },
      { id: "performance" as const, label: "业绩走势", icon: LineChart },
      ...(overview?.is_held || held
        ? [{ id: "holding" as const, label: "我的收益", icon: WalletCards }]
        : []),
    ],
    [held, overview?.is_held],
  );

  const relationChange = intraday?.close_change_percent ??
    intraday?.points.at(-1)?.percent ?? null;
  const holdingDailyEstimated = Boolean(
    held?.daily_return_is_estimated ?? held?.daily_return_percent_source === "sector_estimate",
  );

  return (
    <div
      className="fixed inset-0 z-[65] flex items-end justify-center bg-slate-950/45 sm:items-center sm:p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fund-research-title"
        className="flex max-h-[100dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-[22px] bg-white shadow-2xl sm:max-h-[min(92dvh,760px)] sm:rounded-[22px]"
      >
        <header className="relative shrink-0 bg-[var(--brand-strong)] px-4 pb-3 pt-3 text-white sm:px-5">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="touch-target absolute right-3 top-3 inline-flex items-center justify-center rounded-full text-white/80 hover:bg-white/10 hover:text-white"
            aria-label="关闭基金详情"
          >
            <X size={22} />
          </button>
          <div className="pr-12">
            <h2 id="fund-research-title" className="truncate text-lg font-bold sm:text-xl">
              {overview?.fund_name ?? fund.fund_name}
            </h2>
            <p className="mt-0.5 text-xs font-medium tabular-nums text-white/65">{fund.fund_code}</p>
          </div>

          {overview ? (
            <div className="mt-3 grid grid-cols-3 divide-x divide-white/15 rounded-xl bg-white/8 py-2.5">
              <div className="px-2 text-center">
                <p className="text-[11px] text-white/60">官方日涨幅 · {overview.nav_date ?? "待更新"}</p>
                <strong className="mt-0.5 block text-base tabular-nums sm:text-lg">
                  {signedPercent(overview.official_daily_return_percent)}
                </strong>
              </div>
              <div className="px-2 text-center">
                <p className="text-[11px] text-white/60">近1年</p>
                <strong className="mt-0.5 block text-base tabular-nums sm:text-lg">
                  {signedPercent(overview.returns.one_year_percent)}
                </strong>
              </div>
              <div className="px-2 text-center">
                <p className="text-[11px] text-white/60">单位净值</p>
                <strong className="mt-0.5 block text-base tabular-nums sm:text-lg">
                  {overview.latest_nav?.toFixed(4) ?? "--"}
                </strong>
              </div>
            </div>
          ) : null}
        </header>

        {overview ? (
          <nav className="shrink-0 overflow-x-auto border-b border-slate-200 bg-white px-2 sm:px-4" aria-label="基金详情分区">
            <div className="flex min-w-max">
              {tabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    aria-current={activeTab === tab.id ? "page" : undefined}
                    className={`flex min-h-12 items-center gap-1.5 border-b-2 px-3 text-sm font-semibold transition sm:px-5 ${
                      activeTab === tab.id
                        ? "border-[var(--brand)] text-[var(--brand-strong)]"
                        : "border-transparent text-slate-500 hover:text-slate-800"
                    }`}
                  >
                    <Icon size={16} />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </nav>
        ) : null}

        <main className="min-h-0 flex-1 overflow-y-auto bg-slate-50/70 p-3 sm:p-4">
          {loading ? (
            <div className="flex min-h-72 flex-col items-center justify-center text-sm text-slate-500" role="status">
              <Loader2 size={26} className="mb-3 animate-spin text-[var(--brand)]" />
              正在加载基金信息…
            </div>
          ) : error ? (
            <div className="mx-auto flex min-h-72 max-w-md flex-col items-center justify-center text-center">
              <AlertCircle size={30} className="mb-3 text-rose-500" />
              <p className="text-sm text-rose-700">{error}</p>
              <button type="button" onClick={loadOverview} className="btn-secondary mt-5 min-h-11 px-5">
                <RefreshCw size={16} />
                重新加载
              </button>
            </div>
          ) : overview ? (
            <>
              {activeTab === "overview" ? (
                <div className="space-y-3">
                  <section className="rounded-2xl border border-slate-200 bg-white p-4">
                    <h3 className="text-base font-bold text-slate-900">阶段收益</h3>
                    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                      {[
                        ["近1月", overview.returns.one_month_percent],
                        ["近3月", overview.returns.three_month_percent],
                        ["近6月", overview.returns.six_month_percent],
                        ["近1年", overview.returns.one_year_percent],
                      ].map(([label, value]) => (
                        <div key={String(label)} className="rounded-xl bg-slate-50 px-3 py-2.5">
                          <p className="text-xs text-slate-500">{label}</p>
                          <strong className={`mt-1 block text-lg tabular-nums ${percentTone(value as number | null)}`}>
                            {signedPercent(value as number | null)}
                          </strong>
                        </div>
                      ))}
                    </div>
                  </section>

                  <section className="rounded-2xl border border-slate-200 bg-white p-4">
                    <h3 className="text-base font-bold text-slate-900">基金资料</h3>
                    <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-3 text-sm sm:grid-cols-3">
                      <div><dt className="text-xs text-slate-500">基金类型</dt><dd className="mt-1 font-semibold text-slate-800">{overview.fund_type ?? "--"}</dd></div>
                      <div><dt className="text-xs text-slate-500">管理费</dt><dd className="mt-1 font-semibold text-slate-800">{overview.management_fee ?? "--"}</dd></div>
                      <div><dt className="text-xs text-slate-500">基金规模</dt><dd className="mt-1 font-semibold text-slate-800">{overview.fund_scale_yi != null ? `${overview.fund_scale_yi.toFixed(2)} 亿元` : "--"}</dd></div>
                      <div><dt className="text-xs text-slate-500">规模日期</dt><dd className="mt-1 font-semibold text-slate-800">{overview.fund_scale_as_of ?? "--"}</dd></div>
                      <div><dt className="text-xs text-slate-500">近1年最大回撤</dt><dd className="mt-1 font-semibold text-slate-800">{signedPercent(overview.max_drawdown_1y_percent)}</dd></div>
                      <div><dt className="text-xs text-slate-500">净值日期</dt><dd className="mt-1 font-semibold text-slate-800">{overview.nav_date ?? "--"}</dd></div>
                    </dl>
                  </section>
                </div>
              ) : null}

              {activeTab === "relation" ? (
                overview.relation.price_proxy_eligible ? (
                  <section className="rounded-2xl border border-slate-200 bg-white p-3.5 sm:p-4">
                    <div className="flex items-end justify-between gap-3 border-b border-slate-100 pb-3">
                      <div className="min-w-0">
                        <p className="text-xs text-slate-500">关联板块</p>
                        <h3 className="mt-1 truncate text-lg font-bold text-slate-950">
                          {overview.relation.source_name ?? overview.relation.label ?? "--"}
                        </h3>
                        {overview.relation.source_code ? (
                          <p className="mt-0.5 text-xs tabular-nums text-slate-400">{overview.relation.source_code}</p>
                        ) : null}
                      </div>
                      <div className="shrink-0 text-right">
                        <p className="text-xs tabular-nums text-slate-500">
                          日期 {intraday?.session_date ?? overview.nav_date ?? "--"}
                        </p>
                        <strong className={`mt-1 block text-xl tabular-nums ${percentTone(relationChange)}`}>
                          {signedPercent(relationChange)}
                        </strong>
                      </div>
                    </div>
                    {intradayLoading ? (
                      <div className="flex h-[145px] items-center justify-center text-sm text-slate-500">
                        <Loader2 size={18} className="mr-2 animate-spin" />加载板块行情…
                      </div>
                    ) : (
                      <div className="pt-2">
                        <IntradayPercentChart points={intraday?.points ?? []} height={145} />
                      </div>
                    )}
                  </section>
                ) : (
                  <section className="flex min-h-48 flex-col items-center justify-center rounded-2xl border border-slate-200 bg-white px-6 text-center">
                    <Layers3 size={26} className="mb-3 text-slate-300" />
                    <h3 className="text-base font-bold text-slate-900">暂无可用关联板块</h3>
                    <p className="mt-1 text-sm text-slate-500">该基金暂不能可靠对应到单一板块</p>
                  </section>
                )
              ) : null}

              <section
                hidden={activeTab !== "performance"}
                className="rounded-2xl border border-slate-200 bg-white p-3 sm:p-4"
              >
                  <div className="mb-3 px-1">
                    <h3 className="text-base font-bold text-slate-900">基金与参考基准走势</h3>
                  </div>
                  <PerformanceTrendPanel
                    fundCode={overview.fund_code}
                    fundName={overview.fund_name}
                    benchmarkSymbol={overview.performance_benchmark?.symbol ?? null}
                    benchmarkName={overview.performance_benchmark?.name ?? null}
                    showTransactions={overview.is_held}
                    initialFundHistory={overview.nav_history}
                    initialFundHistoryCoverageDays={260}
                    chartHeight={170}
                  />
              </section>

              {activeTab === "holding" && held ? (
                <div className="space-y-4">
                  <section className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
                    <div className="flex items-center justify-between gap-3">
                      <div><p className="text-xs text-slate-500">当前持有金额</p><strong className="mt-1 block text-2xl tabular-nums text-slate-950">{currency(held.holding_amount)}</strong></div>
                      <span className="rounded-full bg-blue-50 px-3 py-1.5 text-xs font-bold text-[var(--brand-strong)]">已在持仓</span>
                    </div>
                    <div className="mt-5 grid grid-cols-3 divide-x divide-slate-100 rounded-xl bg-slate-50 py-3 text-center">
                      <div className="px-2"><p className="text-[11px] text-slate-500">持有收益</p><strong className={`mt-1 block text-sm tabular-nums ${percentTone(held.holding_profit)}`}>{currency(held.holding_profit)}</strong></div>
                      <div className="px-2"><p className="text-[11px] text-slate-500">持有收益率</p><strong className={`mt-1 block text-sm tabular-nums ${percentTone(held.holding_return_percent)}`}>{signedPercent(held.holding_return_percent)}</strong></div>
                      <div className="px-2"><p className="text-[11px] text-slate-500">{holdingDailyEstimated ? "估算当日收益" : "当日收益"}</p><strong className={`mt-1 block text-sm tabular-nums ${percentTone(held.daily_profit)}`}>{holdingDailyEstimated && held.daily_profit != null ? "≈" : ""}{currency(held.daily_profit)}</strong></div>
                    </div>
                    {holdingDailyEstimated ? (
                      <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-800">
                        ≈板块参考估算，等待官方净值后会切换为正式当日收益。
                      </p>
                    ) : null}
                  </section>
                </div>
              ) : null}
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}
