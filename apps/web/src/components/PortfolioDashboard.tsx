"use client";

import { useState } from "react";
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

const RANGE_TABS: Array<{ id: ProfitRange; label: string }> = [
  { id: "today", label: "当日" },
  { id: "week", label: "本周" },
  { id: "month", label: "本月" },
  { id: "year", label: "今年" },
  { id: "all", label: "全部" },
];

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

export function PortfolioDashboard({
  fallbackSummary = null,
}: {
  fallbackSummary?: PortfolioDashboardData["summary"] | null;
}) {
  const [profitRange, setProfitRange] = useState<ProfitRange>("today");
  const [calendarYear, setCalendarYear] = useState(() => new Date().getFullYear());
  const [calendarMonth, setCalendarMonth] = useState(() => new Date().getMonth() + 1);
  const [calendarShowReturn, setCalendarShowReturn] = useState(false);
  const [showReturnHeader, setShowReturnHeader] = useState(false);
  const [showFactorScores, setShowFactorScores] = useState(false);
  const [showEvidenceOverview, setShowEvidenceOverview] = useState(false);

  const cacheKey = buildClientCacheKey("portfolio-dashboard", profitRange, calendarYear, calendarMonth);
  const staleTimeMs = profitRange === "today" ? 60_000 : 300_000;

  const {
    data,
    error: fetchError,
  } = useCachedFetch<PortfolioDashboardData>({
    cacheKey,
    staleTimeMs,
    storage: "session",
    fetcher: () =>
      fetchPortfolioDashboard({
        range: profitRange,
        calendarYear,
        calendarMonth,
      }),
  });

  const error = fetchError;

  const summary = data?.summary ?? fallbackSummary ?? null;
  const footer = data?.profit_trend_footer;
  const headerValue = showReturnHeader ? summary?.daily_return_percent : summary?.daily_profit;
  const displayTone = showReturnHeader ? summary?.daily_return_percent : summary?.daily_profit;
  const alpha = footer?.alpha_percent;
  const portfolioLineColor =
    (footer?.portfolio_return_percent ?? 0) > 0
      ? "#e11d48"
      : (footer?.portfolio_return_percent ?? 0) < 0
        ? "#059669"
        : "#64748b";

  return (
    <div className="pl-page mx-auto max-w-3xl">
      <div className="section-card briefing-hero overflow-hidden">
        <div className="pl-hero !rounded-none !border-0 !bg-transparent">
        <div className="pl-hero-label">
          {profitRange === "today" ? "当日收益" : `${RANGE_TABS.find((t) => t.id === profitRange)?.label ?? ""}累计`}
        </div>
        <div className={`pl-hero-value ${profitClass(displayTone)}`}>
          {showReturnHeader ? formatPercent(headerValue) : formatMoney(headerValue)}
        </div>
        {!showReturnHeader && summary?.daily_return_percent != null && profitRange === "today" ? (
          <div className={`pl-hero-sub ${profitClass(summary.daily_return_percent)}`}>
            {formatPercent(summary.daily_return_percent)}
          </div>
        ) : null}
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
        </div>
      </div>

      <div className="section-card mt-3 overflow-hidden">
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

      {error ? (
        <div className="mt-3 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

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

        <ProfitAnalysisTrendChart trend={data?.profit_trend} />

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

      <PortfolioRiskMetricsPanel />

      <section className="pl-panel section-card">
        <div className="pl-panel-head">
          <div className="pl-panel-title">持仓因子体检</div>
          <button
            type="button"
            className="risk-corr-toggle"
            aria-expanded={showFactorScores}
            onClick={() => setShowFactorScores((value) => !value)}
          >
            {showFactorScores ? "收起因子评分" : "展开因子评分"}
          </button>
        </div>
        <p className="factor-intro">
          把每只持仓放进排行榜可比池里横着比：动量、风险调整收益、回撤控制、规模——看清谁在拖后腿。
        </p>
        <PortfolioFactorScoresPanel enabled={showFactorScores} />
      </section>

      <section className="pl-panel section-card">
        <div className="pl-panel-head">
          <div className="pl-panel-title">组合证据总览</div>
          <button
            type="button"
            className="risk-corr-toggle"
            aria-expanded={showEvidenceOverview}
            onClick={() => setShowEvidenceOverview((value) => !value)}
          >
            {showEvidenceOverview ? "收起证据总览" : "展开证据总览"}
          </button>
        </div>
        <p className="factor-intro">
          把每只持仓的三路量化置信（因子IC、板块信号、风险样本）聚成一张组合体检：多少市值的建议有可回测背书。
        </p>
        <PortfolioEvidenceOverviewPanel enabled={showEvidenceOverview} />
      </section>

      <div className="mt-3 grid gap-3">
        <ProfitLossCalendar
          calendar={data?.profit_calendar}
          showReturnPercent={calendarShowReturn}
          onToggleMode={() => setCalendarShowReturn((value) => !value)}
          onMonthChange={(year, month) => {
            setCalendarYear(year);
            setCalendarMonth(month);
          }}
        />
        <DailyProfitTop5
          gainers={data?.daily_top5?.gainers ?? []}
          losers={data?.daily_top5?.losers ?? []}
        />
        <section className="pl-panel section-card">
          <div className="pl-panel-title mb-3">持仓分布</div>
          <HoldingDonutChart rows={data?.allocation ?? []} />
        </section>
      </div>
    </div>
  );
}

const INDEX_COLOR = "#5B8DEF";
