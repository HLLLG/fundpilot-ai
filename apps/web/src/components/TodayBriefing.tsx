"use client";

import {
  Activity,
  ArrowRight,
  ChevronRight,
  Eye,
  EyeOff,
  FileText,
  Plus,
  RefreshCw,
  ScanLine,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import type { Holding, PortfolioSummary, Report } from "@/lib/api";
import {
  cnProfitClass,
  displayableHoldings,
  formatSignedMoney,
  formatSignedPercent,
  sumDailyProfit,
  sumHoldingAmount,
} from "@/lib/holdingMetrics";
import { getDailyProfit, getEstimatedDailyReturnPercent } from "@/lib/holdingDisplay";
import { holdingDisplaySectorLabel } from "@/lib/profileSector";
import { loadAmountsHidden, saveAmountsHidden } from "@/lib/storage";
import {
  extractBriefingDecisions,
  extractBriefingSummary,
  findTodayReport,
  greetingForHour,
  pickTopHoldings,
  resolveSectorPulse,
} from "@/lib/todayBriefing";
import { BriefingDecisionCards } from "@/components/BriefingDecisionCards";
import { BriefingChatPanel } from "@/components/BriefingChatPanel";
import type { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";
import { useMemo, useState } from "react";

type SectorRefreshControl = ReturnType<typeof useSectorQuoteRefresh>;

export type TodayBriefingProps = {
  holdings: Holding[];
  reports: Report[];
  portfolioSummary?: PortfolioSummary | null;
  sectorRefresh: SectorRefreshControl;
  refreshedAt?: string | null;
  isLoading?: boolean;
  onNavigateTab: (tab: "holdings" | "report") => void;
  onAddHolding?: () => void;
  onSelectHolding?: (index: number) => void;
};

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

function formatBalance(value: number | null | undefined, hidden: boolean) {
  if (hidden) {
    return "****";
  }
  if (value == null) {
    return "—";
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function TodayBriefing({
  holdings,
  reports,
  portfolioSummary,
  sectorRefresh,
  refreshedAt,
  isLoading = false,
  onNavigateTab,
  onAddHolding,
  onSelectHolding,
}: TodayBriefingProps) {
  const [amountsHidden, setAmountsHidden] = useState(() => loadAmountsHidden());
  const [showReturn, setShowReturn] = useState(false);
  const { isRefreshing, refresh, sectorMetaByFundCode } = sectorRefresh;

  const displayHoldings = useMemo(() => displayableHoldings(holdings), [holdings]);
  const todayReport = useMemo(() => findTodayReport(reports), [reports]);
  const briefing = useMemo(() => extractBriefingSummary(todayReport), [todayReport]);
  const decisions = useMemo(() => extractBriefingDecisions(todayReport, 3), [todayReport]);
  const sectorPulse = useMemo(() => resolveSectorPulse(holdings), [holdings]);
  const topHoldings = useMemo(() => pickTopHoldings(holdings, 3), [holdings]);

  const totalAssets = sumHoldingAmount(displayHoldings) || portfolioSummary?.total_assets || null;
  const dailyProfit = displayHoldings.length > 0 ? sumDailyProfit(displayHoldings) : null;
  const dailyReturn = accountDailyReturnPercent(dailyProfit, totalAssets);
  const displayTone = showReturn ? dailyReturn : dailyProfit;

  if (!displayHoldings.length) {
    return (
      <section className="animate-fade-up mx-auto w-full max-w-2xl">
        <div className="briefing-hero section-card overflow-hidden p-6 sm:p-8">
          <div className="empty-state !py-8">
            <span className="empty-state-icon">
              <ScanLine size={26} strokeWidth={2.2} />
            </span>
            <p className="text-sm font-semibold text-[var(--muted)]">{greetingForHour()}，欢迎用好基灵</p>
            <h2 className="font-display text-2xl font-extrabold tracking-tight text-slate-950">
              截张图，30 秒看懂你的基金
            </h2>
            <p className="max-w-sm text-sm leading-6 text-slate-500">
              上传支付宝或养基宝持仓截图，自动识别基金与收益，并生成每日 AI 简报。
            </p>
            {onAddHolding ? (
              <button type="button" onClick={onAddHolding} className="btn-primary mt-2 !px-5 !py-2.5 !text-sm">
                <Plus size={16} />
                上传截图 / 新增持有
              </button>
            ) : null}
            <p className="text-xs text-slate-400">数据仅本地识别，不上传原始截图</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="animate-fade-up mx-auto flex w-full max-w-2xl flex-col gap-4">
      {/* Hero */}
      <div className="briefing-hero section-card overflow-hidden">
        <div className="flex items-start justify-between gap-3 px-5 pt-5">
          <div>
            <p className="text-sm font-semibold text-[var(--muted)]">{greetingForHour()}</p>
            <h1 className="font-display mt-0.5 text-xl font-extrabold tracking-tight text-slate-950">
              今日简报
            </h1>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => {
                setAmountsHidden((current) => {
                  const next = !current;
                  saveAmountsHidden(next);
                  return next;
                });
              }}
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-slate-400 hover:bg-white/80"
              aria-label={amountsHidden ? "显示金额" : "隐藏金额"}
            >
              {amountsHidden ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
            <button
              type="button"
              onClick={() => void refresh(true, "accurate")}
              disabled={isRefreshing}
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-slate-500 hover:bg-white/80 disabled:opacity-50"
              aria-label="刷新板块行情"
            >
              <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowReturn((v) => !v)}
          className="mt-4 w-full px-5 pb-5 text-left transition hover:opacity-90"
        >
          <div className="text-xs font-semibold text-[var(--muted)]">
            {showReturn ? "当日收益率" : "当日收益（元）"}
          </div>
          <div className={`kpi-value mt-1 text-[2.75rem] leading-none ${cnProfitClass(displayTone)}`}>
            {showReturn ? formatSignedPercent(dailyReturn) : formatSignedMoney(dailyProfit)}
          </div>
          <div className="mt-3 flex items-center gap-3 text-sm text-[var(--muted)]">
            <span>
              总资产{" "}
              <strong className="font-bold text-slate-800">{formatBalance(totalAssets, amountsHidden)}</strong>
            </span>
            {refreshedAt ? <span className="text-xs">· 已更新</span> : null}
          </div>
        </button>
      </div>

      {/* AI Brief */}
      <div className="briefing-ai-card section-card p-5">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[var(--brand-soft)] text-[var(--brand-strong)]">
            <Sparkles size={20} strokeWidth={2.3} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-extrabold text-slate-950">AI 一句话</h2>
              {briefing.hasTodayReport && briefing.riskLabel ? (
                <span className="badge">{briefing.riskLabel}</span>
              ) : null}
              {briefing.hasTodayReport && briefing.actionLabel ? (
                <span className="badge-accent">{briefing.actionLabel}</span>
              ) : null}
            </div>
            <p className="mt-2 text-[0.9375rem] leading-7 text-slate-700">{briefing.headline}</p>
            {briefing.detail ? (
              <p className="mt-2 text-sm leading-6 text-slate-500">{briefing.detail}</p>
            ) : null}
            {briefing.focusFund ? (
              <p className="mt-2 text-sm font-semibold text-[var(--brand-strong)]">
                重点关注：{briefing.focusFund}
                {briefing.focusAction ? ` · ${briefing.focusAction}` : ""}
              </p>
            ) : null}
            <button
              type="button"
              onClick={() => onNavigateTab("report")}
              className="mt-4 inline-flex items-center gap-1.5 text-sm font-bold text-[var(--brand-strong)] hover:underline"
            >
              {briefing.hasTodayReport ? (
                <>
                  <FileText size={15} />
                  阅读完整日报
                </>
              ) : (
                <>
                  <Sparkles size={15} />
                  生成今日日报
                </>
              )}
              <ArrowRight size={15} />
            </button>
          </div>
        </div>
      </div>

      {briefing.hasTodayReport ? (
        <BriefingDecisionCards
          decisions={decisions}
          onViewFullReport={() => onNavigateTab("report")}
        />
      ) : null}

      {todayReport ? (
        <BriefingChatPanel reportId={todayReport.id} reportTitle={todayReport.title} />
      ) : null}

      {/* Sector pulse */}
      {sectorPulse ? (
        <div className="section-card flex items-center gap-3 p-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent-strong)]">
            <Activity size={18} strokeWidth={2.3} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-xs font-bold uppercase tracking-wide text-[var(--muted)]">板块脉搏</div>
            <div className="mt-0.5 flex flex-wrap items-center gap-2">
              <span className="font-bold text-slate-900">{sectorPulse.sectorName}</span>
              <span
                className={`inline-flex items-center gap-0.5 text-sm font-extrabold tabular-nums ${sectorPulse.returnPercent >= 0 ? "profit-up" : "profit-down"}`}
              >
                {sectorPulse.returnPercent >= 0 ? (
                  <TrendingUp size={14} strokeWidth={2.5} />
                ) : (
                  <TrendingDown size={14} strokeWidth={2.5} />
                )}
                {formatSignedPercent(sectorPulse.returnPercent)}
              </span>
              {sectorPulse.fundName ? (
                <span className="text-xs text-slate-400">关联 {sectorPulse.fundName}</span>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {/* Quick holdings */}
      <div className="section-card overflow-hidden">
        <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3.5">
          <h2 className="section-title">持仓快览</h2>
          <span className="text-xs font-semibold text-slate-400">按当日收益</span>
        </div>
        <ul className="divide-y divide-[var(--line)]">
          {topHoldings.map((holding) => {
            const index = holdings.findIndex(
              (item) => item.fund_code === holding.fund_code && item.fund_name === holding.fund_name,
            );
            const daily = getDailyProfit(holding);
            const dailyPct = getEstimatedDailyReturnPercent(holding);
            const sectorMeta = sectorMetaByFundCode[holding.fund_code];
            const sectorLabel = holdingDisplaySectorLabel(holding, sectorMeta);

            return (
              <li key={`${holding.fund_code}-${holding.fund_name}`}>
                <button
                  type="button"
                  onClick={() => onSelectHolding?.(index >= 0 ? index : 0)}
                  className="briefing-holding-row flex w-full items-center gap-3 px-4 py-3.5 text-left transition hover:bg-slate-50/80"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-bold text-slate-900">{holding.fund_name}</div>
                    <span className="mini-chip mt-1">{sectorLabel}</span>
                  </div>
                  <div className="text-right">
                    <div className={`font-display text-base font-extrabold tabular-nums ${cnProfitClass(daily)}`}>
                      {formatSignedMoney(daily)}
                    </div>
                    {dailyPct != null ? (
                      <div className={`text-xs font-bold tabular-nums ${cnProfitClass(dailyPct)}`}>
                        {formatSignedPercent(dailyPct)}
                      </div>
                    ) : null}
                  </div>
                  <ChevronRight size={16} className="shrink-0 text-slate-300" />
                </button>
              </li>
            );
          })}
        </ul>
        <div className="border-t border-[var(--line)] px-4 py-3">
          <button
            type="button"
            onClick={() => onNavigateTab("holdings")}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl py-2.5 text-sm font-bold text-[var(--brand-strong)] hover:bg-[var(--brand-soft)]"
          >
            查看全部 {displayHoldings.length} 只持仓
            <ArrowRight size={15} />
          </button>
        </div>
      </div>

      {isLoading ? (
        <p className="text-center text-xs text-slate-400">正在同步持仓数据…</p>
      ) : null}
    </section>
  );
}
