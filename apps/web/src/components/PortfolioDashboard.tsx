"use client";

import { useId, useState } from "react";
import type { PortfolioDashboardData, ProfitRange } from "@/lib/api";
import { fetchPortfolioDashboard } from "@/lib/api";
import { buildClientCacheKey } from "@/lib/clientCache";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { DailyProfitTop5 } from "@/components/DailyProfitTop5";
import { HoldingDonutChart } from "@/components/HoldingDonutChart";
import { ProfitAnalysisTrendChart } from "@/components/ProfitAnalysisTrendChart";
import { ProfitLossCalendar } from "@/components/ProfitLossCalendar";
import { PortfolioRiskMetricsPanel } from "@/components/PortfolioRiskMetricsPanel";
import { PortfolioFactorScoresPanel } from "@/components/PortfolioFactorScoresPanel";
import { PortfolioEvidenceOverviewPanel } from "@/components/PortfolioEvidenceOverviewPanel";
import { FactorIcStatusBadge } from "@/components/FactorIcStatusBadge";
import { InlineNotice } from "@/components/InlineNotice";

const RANGE_TABS: Array<{ id: ProfitRange; label: string }> = [
  { id: "today", label: "当日" },
  { id: "week", label: "本周" },
  { id: "month", label: "本月" },
  { id: "year", label: "今年" },
  { id: "all", label: "全部" },
];

const RANGE_HERO_LABELS: Record<Exclude<ProfitRange, "today">, string> = {
  week: "本周累计收益率",
  month: "本月累计收益率",
  year: "今年累计收益率",
  all: "历史累计收益率",
};

type PortfolioHeroDisplay = {
  label: string;
  value: number | null | undefined;
  valueFormat: "money" | "percent";
  secondaryPercent: number | null | undefined;
  showMetricToggle: boolean;
  explanation: string;
};

export function buildPortfolioHeroDisplay({
  profitRange,
  showTodayReturn,
  summary,
  footer,
}: {
  profitRange: ProfitRange;
  showTodayReturn: boolean;
  summary: PortfolioDashboardData["summary"] | null;
  footer: PortfolioDashboardData["profit_trend_footer"] | null | undefined;
}): PortfolioHeroDisplay {
  if (profitRange !== "today") {
    return {
      label: RANGE_HERO_LABELS[profitRange],
      value: footer?.portfolio_return_percent,
      valueFormat: "percent",
      secondaryPercent: null,
      showMetricToggle: false,
      explanation: "口径：所选区间内每日收益率按复利累计；不等同于收益金额。",
    };
  }

  if (showTodayReturn) {
    return {
      label: "当日收益率",
      value: summary?.daily_return_percent,
      valueFormat: "percent",
      secondaryPercent: null,
      showMetricToggle: true,
      explanation: "口径：最新交易日收益；盘中数据可能为估算值。",
    };
  }

  return {
    label: "当日收益",
    value: summary?.daily_profit,
    valueFormat: "money",
    secondaryPercent: summary?.daily_return_percent,
    showMetricToggle: true,
    explanation: "口径：最新交易日收益；盘中数据可能为估算值。",
  };
}

export function isPortfolioDataForRange(
  data: PortfolioDashboardData | null,
  profitRange: ProfitRange,
): data is PortfolioDashboardData {
  if (!data) {
    return false;
  }
  if (data.profit_range != null) {
    return data.profit_range === profitRange;
  }
  return profitRange === "today" && data.profit_trend?.kind === "intraday";
}

export function portfolioDashboardDataDate(data: PortfolioDashboardData | null): string | null {
  if (!data) {
    return null;
  }
  const trend = data.profit_trend;
  if (trend?.kind === "intraday" && trend.trade_date) {
    return trend.trade_date.slice(0, 10);
  }
  const latestTrendDate = [...(trend?.points ?? [])]
    .reverse()
    .find((point) => point.date)?.date;
  const rawDate = latestTrendDate ?? data.latest_snapshot_date ?? data.summary.updated_at;
  return rawDate ? rawDate.slice(0, 10) : null;
}

export function hasPortfolioDashboardContent(data: PortfolioDashboardData | null): boolean {
  if (!data) {
    return false;
  }
  const summary = data.summary;
  const hasSummary =
    summary.total_assets != null ||
    summary.daily_profit != null ||
    summary.daily_return_percent != null ||
    (summary.holding_count ?? 0) > 0;
  const hasDailyContributors =
    (data.daily_top5?.gainers.length ?? 0) > 0 || (data.daily_top5?.losers.length ?? 0) > 0;
  return (
    hasSummary ||
    data.snapshot_count > 0 ||
    data.allocation.length > 0 ||
    hasDailyContributors
  );
}

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatPercent(value: number | null | undefined) {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

function profitClass(value: number | null | undefined) {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

export function buildPortfolioStatusLine(
  value: number | null | undefined,
  profitRange: ProfitRange,
): string {
  const period = profitRange === "today" ? "今天" : "这段时间";
  if (value == null) {
    return `先看${period}的收益变化，再决定是否需要调整。`;
  }
  if (value <= -1) {
    return `${period}下跌较明显，先找出主要拖累。`;
  }
  if (value < 0) {
    return `${period}小幅回落，暂不必急着操作。`;
  }
  if (value >= 1) {
    return `${period}表现较强，继续关注上涨是否过于集中。`;
  }
  if (value > 0) {
    return `${period}小幅上涨，先看主要贡献来自哪里。`;
  }
  return `${period}波动不大，持仓整体平稳。`;
}

export function PortfolioDashboard({
  userId,
  fallbackSummary = null,
}: {
  userId: number | null;
  fallbackSummary?: PortfolioDashboardData["summary"] | null;
}) {
  const professionalDetailsId = useId().replace(/:/g, "");
  const [profitRange, setProfitRange] = useState<ProfitRange>("today");
  const [calendarYear, setCalendarYear] = useState(() => new Date().getFullYear());
  const [calendarMonth, setCalendarMonth] = useState(() => new Date().getMonth() + 1);
  const [calendarShowReturn, setCalendarShowReturn] = useState(false);
  const [showReturnHeader, setShowReturnHeader] = useState(false);
  const [showDeepAnalysis, setShowDeepAnalysis] = useState(false);
  const [showFactorScores, setShowFactorScores] = useState(false);
  const [showEvidenceOverview, setShowEvidenceOverview] = useState(false);

  const cacheKey = buildClientCacheKey(
    "portfolio-dashboard",
    userId ?? "anonymous",
    profitRange,
    calendarYear,
    calendarMonth,
  );
  const staleTimeMs = profitRange === "today" ? 60_000 : 300_000;

  const {
    data,
    error: fetchError,
    loading,
    revalidating,
    refresh,
  } = useCachedFetch<PortfolioDashboardData>({
    cacheKey,
    staleTimeMs,
    storage: "session",
    enabled: userId != null,
    fetcher: () =>
      fetchPortfolioDashboard({
        range: profitRange,
        calendarYear,
        calendarMonth,
      }),
  });

  const currentData = isPortfolioDataForRange(data, profitRange) ? data : null;
  const summary = currentData?.summary ?? (profitRange === "today" ? fallbackSummary : null);
  const footer = currentData?.profit_trend_footer;
  const hero = buildPortfolioHeroDisplay({
    profitRange,
    showTodayReturn: showReturnHeader,
    summary,
    footer,
  });
  const statusLine = buildPortfolioStatusLine(
    profitRange === "today" ? summary?.daily_return_percent : footer?.portfolio_return_percent,
    profitRange,
  );
  const dataDate = portfolioDashboardDataDate(currentData);
  const awaitingCurrentRange = loading || (data != null && currentData == null);
  const hasCurrentContent = hasPortfolioDashboardContent(currentData);
  const hasFallbackSummary =
    currentData == null && profitRange === "today" && fallbackSummary != null;
  const showAnalysisContent = currentData != null && hasCurrentContent;
  const updatingWithContent = (loading || revalidating) && showAnalysisContent;
  const retryAction = { label: "重试", onClick: () => void refresh() };
  const alpha = footer?.alpha_percent;
  const portfolioLineColor =
    (footer?.portfolio_return_percent ?? 0) > 0
      ? "#e11d48"
      : (footer?.portfolio_return_percent ?? 0) < 0
        ? "#059669"
        : "#64748b";

  return (
    <div className="pl-page mx-auto max-w-5xl">
      <div className="analysis-hero briefing-hero overflow-hidden">
        <div className="pl-hero !rounded-none !border-0 !bg-transparent">
          <p className="analysis-verdict">{statusLine}</p>
          <div className="pl-hero-label">{hero.label}</div>
          <div className={`pl-hero-value ${profitClass(hero.value)}`}>
            {hero.valueFormat === "percent" ? formatPercent(hero.value) : formatMoney(hero.value)}
          </div>
          {hero.secondaryPercent != null ? (
            <div className={`pl-hero-sub ${profitClass(hero.secondaryPercent)}`}>
              {formatPercent(hero.secondaryPercent)}
            </div>
          ) : null}
          {hero.showMetricToggle ? (
            <div className="pl-toggle">
              <button
                type="button"
                aria-pressed={!showReturnHeader}
                className="pl-toggle-btn"
                onClick={() => setShowReturnHeader(false)}
              >
                收益额
              </button>
              <button
                type="button"
                aria-pressed={showReturnHeader}
                className="pl-toggle-btn"
                onClick={() => setShowReturnHeader(true)}
              >
                收益率
              </button>
            </div>
          ) : null}
          <p
            className="mx-auto mt-2 max-w-xl text-xs leading-5 text-slate-500"
            aria-live="polite"
          >
            {awaitingCurrentRange ? "正在读取所选区间数据…" : hero.explanation}
            {dataDate ? ` · 数据截至 ${dataDate}` : ""}
          </p>
          {showAnalysisContent ? (
            <div className="analysis-key-metrics" aria-label="本期三个关键数字">
              <div>
                <span>我的收益率</span>
                <strong className={profitClass(footer?.portfolio_return_percent)}>
                  {formatPercent(footer?.portfolio_return_percent)}
                </strong>
              </div>
              <div>
                <span>同期大盘</span>
                <strong className={profitClass(footer?.index_return_percent)}>
                  {formatPercent(footer?.index_return_percent)}
                </strong>
              </div>
              <div>
                <span>领先 / 落后</span>
                <strong className={profitClass(alpha)}>{formatPercent(alpha)}</strong>
              </div>
            </div>
          ) : null}
          {showAnalysisContent ? (
            <button
              type="button"
              className="analysis-primary-action"
              onClick={() =>
                document
                  .getElementById("portfolio-contributors")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              查看哪些基金影响最大
            </button>
          ) : null}
        </div>
      </div>

      <div className="analysis-range mt-3 overflow-hidden">
      <div className="pl-range-bar !rounded-none !border-0 !border-t !border-[var(--line)]" role="tablist" aria-label="时间范围">
        {RANGE_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={profitRange === tab.id}
            onClick={() => setProfitRange(tab.id)}
            className="pl-range-tab"
          >
            {tab.label}
          </button>
        ))}
      </div>
      </div>

      {fetchError ? (
        <InlineNotice
          tone={showAnalysisContent ? "warning" : "error"}
          message={
            showAnalysisContent
              ? `最新盈亏数据更新失败，继续显示${dataDate ? `截至 ${dataDate} 的` : "上次成功获取的"}数据。${fetchError}`
              : `盈亏分析加载失败：${fetchError}`
          }
          action={retryAction}
          className="mt-3"
        />
      ) : updatingWithContent ? (
        <InlineNotice
          tone="info"
          message={`正在更新盈亏分析，当前继续显示${dataDate ? `截至 ${dataDate} 的` : "已有"}数据。`}
          className="mt-3"
        />
      ) : loading && hasFallbackSummary ? (
        <InlineNotice
          tone="info"
          message="正在加载完整盈亏明细，当前先显示最近账户摘要。"
          className="mt-3"
        />
      ) : awaitingCurrentRange && !showAnalysisContent ? (
        <InlineNotice tone="info" message="正在加载所选区间的盈亏分析…" className="mt-3" />
      ) : currentData != null && !hasCurrentContent ? (
        <InlineNotice
          tone="info"
          message="暂无可分析的持仓收益数据。添加持仓并积累至少一份收益快照后，这里会展示趋势、贡献与风险。"
          className="mt-3"
        />
      ) : null}

      {showAnalysisContent ? (
      <div data-testid="portfolio-analysis-content">
      <section className="pl-panel section-card">
        <div className="pl-panel-head">
          <div className="pl-panel-title">收益走势</div>
          <div className="pl-legend">
            <span className="pl-legend-item">
              <span className="pl-legend-dot" style={{ background: portfolioLineColor }} />
              我的收益 {formatPercent(footer?.portfolio_return_percent)}
            </span>
            <span className="pl-legend-item">
              <span className="pl-legend-dot" style={{ background: INDEX_COLOR }} />
              上证 {formatPercent(footer?.index_return_percent)}
            </span>
          </div>
        </div>

        <ProfitAnalysisTrendChart trend={currentData?.profit_trend} />

        <div className="pl-chart-footer">
          <span>
            {profitRange === "today" ? "当日收益率" : "区间累计"}：
            <strong className={profitClass(footer?.portfolio_return_percent)}>
              {formatPercent(footer?.portfolio_return_percent)}
            </strong>
          </span>
          {alpha != null ? (
            <span>
              {alpha >= 0 ? "跑赢" : "跑输"}上证指数：
              <strong className={profitClass(alpha)}>{formatPercent(Math.abs(alpha))}</strong>
            </span>
          ) : null}
        </div>
      </section>

      <div
        id="portfolio-contributors"
        className="mt-3 grid scroll-mt-24 gap-3"
        data-testid="portfolio-daily-insights"
      >
        <DailyProfitTop5
          gainers={currentData?.daily_top5?.gainers ?? []}
          losers={currentData?.daily_top5?.losers ?? []}
        />
        <ProfitLossCalendar
          calendar={currentData?.profit_calendar}
          showReturnPercent={calendarShowReturn}
          onToggleMode={() => setCalendarShowReturn((value) => !value)}
          onMonthChange={(year, month) => {
            setCalendarYear(year);
            setCalendarMonth(month);
          }}
        />
      </div>

      <section className="pl-panel section-card" data-testid="portfolio-allocation-section">
        <h2 className="pl-panel-title mb-3">持仓分布</h2>
        <HoldingDonutChart rows={currentData?.allocation ?? []} />
      </section>

      <section
        className="analysis-deep-section"
        data-testid="deep-analysis-section"
        aria-labelledby={`${professionalDetailsId}-title`}
      >
        <div className="analysis-deep-heading">
          <div>
            <h2 id={`${professionalDetailsId}-title`} className="pl-panel-title">
              深度分析
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              历史最深下跌、分散程度与专业研究依据，适合需要进一步复核时查看。
            </p>
          </div>
          <button
            type="button"
            className="analysis-deep-toggle"
            aria-expanded={showDeepAnalysis}
            aria-controls={`${professionalDetailsId}-content`}
            onClick={() => setShowDeepAnalysis((value) => !value)}
          >
            {showDeepAnalysis ? "收起深度分析" : "展开深度分析"}
          </button>
        </div>

        {showDeepAnalysis ? (
        <div id={`${professionalDetailsId}-content`} className="grid gap-3 border-t border-[var(--line)] pt-3">
          <PortfolioRiskMetricsPanel />

          <section className="pl-panel section-card" data-testid="professional-quant-evidence">
          <div className="mb-3">
            <h3 className="text-sm font-bold text-slate-900">专业研究依据</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              以下内容用于复核研究结论，不影响上方的日常盈亏判断。
            </p>
          </div>
          <div className="rounded-xl border border-[var(--line)] bg-slate-50/60 p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <h3 className="text-sm font-bold text-slate-900">持仓因子体检</h3>
                <FactorIcStatusBadge />
              </div>
              <button
                type="button"
                className="inline-flex min-h-11 items-center justify-center rounded-lg border border-[var(--line)] bg-white px-3 text-xs font-bold text-slate-700 transition hover:border-[var(--brand)] hover:text-[var(--brand-strong)]"
                aria-expanded={showFactorScores}
                aria-controls={`${professionalDetailsId}-factor`}
                onClick={() => setShowFactorScores((value) => !value)}
              >
                {showFactorScores ? "收起因子评分" : "展开因子评分"}
              </button>
            </div>
            <p className="mt-2 text-xs leading-5 text-slate-500">
              横向比较动量、风险调整收益、回撤控制和规模，识别可能拖累组合的持仓。
            </p>
            {showFactorScores ? (
              <div id={`${professionalDetailsId}-factor`} className="mt-3">
                <PortfolioFactorScoresPanel enabled />
              </div>
            ) : null}
          </div>

          <div className="rounded-xl border border-[var(--line)] bg-slate-50/60 p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-sm font-bold text-slate-900">组合证据总览</h3>
              </div>
              <button
                type="button"
                className="inline-flex min-h-11 items-center justify-center rounded-lg border border-[var(--line)] bg-white px-3 text-xs font-bold text-slate-700 transition hover:border-[var(--brand)] hover:text-[var(--brand-strong)]"
                aria-expanded={showEvidenceOverview}
                aria-controls={`${professionalDetailsId}-evidence`}
                onClick={() => setShowEvidenceOverview((value) => !value)}
              >
                {showEvidenceOverview ? "收起证据总览" : "展开证据总览"}
              </button>
            </div>
            <p className="mt-2 text-xs leading-5 text-slate-500">
              聚合因子 IC、板块信号和风险样本，说明当前建议有多少量化证据覆盖。
            </p>
            {showEvidenceOverview ? (
              <div id={`${professionalDetailsId}-evidence`} className="mt-3">
                <PortfolioEvidenceOverviewPanel enabled />
              </div>
            ) : null}
          </div>
          </section>
        </div>
        ) : null}
      </section>
      </div>
      ) : null}
    </div>
  );
}

const INDEX_COLOR = "#5B8DEF";
