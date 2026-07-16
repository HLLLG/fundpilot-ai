"use client";

import { useEffect } from "react";
import { Gauge, Loader2 } from "lucide-react";
import { fetchMarketBreadth, type MarketBreadthSignal } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";
import { FundReturnDistributionPanel } from "@/components/FundReturnDistributionPanel";
import { useCachedFetch } from "@/lib/useCachedFetch";

type MarketBreadthGaugeProps = {
  compact?: boolean;
};

const SENTIMENT_TONE: Record<string, "blue" | "green" | "amber" | "red" | "dark"> = {
  冰点: "red",
  低迷: "amber",
  中性: "blue",
  偏热: "green",
  亢奋: "dark",
};

const MARKET_BREADTH_CACHE_KEY = "diagnostics:market-breadth";
const MARKET_BREADTH_STALE_MS = 60_000;
const MARKET_BREADTH_REFRESH_MS = 5 * 60_000;
const MARKET_BREADTH_INTRADAY_MAX_AGE_MS = 10 * 60_000;

const SOURCE_LABEL: Record<NonNullable<MarketBreadthSignal["source_mode"]>, string> = {
  intraday_live: "盘中准实时",
  intraday_final: "当日收盘快照",
  closing: "收盘历史口径",
  previous_close_fallback: "上一交易日收盘回退",
};

function formatYi(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)} 亿`;
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(1)}%`;
}

function formatCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return String(value);
}

function formatAsOf(value: string | null | undefined, fallback?: string): string {
  if (!value) {
    return fallback ?? "—";
  }
  const normalized = value.replace("T", " ");
  return normalized.length >= 16 ? normalized.slice(0, 16) : normalized;
}

function resolveTone(data: MarketBreadthSignal): "blue" | "green" | "amber" | "red" | "dark" {
  const label = data.breadth_tone ?? "";
  if (label.includes("冰点")) return "red";
  if (label.includes("弱") || label.includes("低迷")) return "amber";
  if (label.includes("强") || label.includes("活跃")) return "green";
  if (label.includes("亢奋")) return "dark";
  return SENTIMENT_TONE[data.sentiment_level ?? ""] ?? "blue";
}

function isExpiredIntradaySnapshot(data: MarketBreadthSignal, nowMs = Date.now()): boolean {
  if (
    data.signal_mode !== "intraday" ||
    data.source_mode === "intraday_final" ||
    !data.as_of_datetime
  ) {
    return false;
  }
  const asOfMs = Date.parse(data.as_of_datetime);
  return Number.isFinite(asOfMs) && nowMs - asOfMs >= MARKET_BREADTH_INTRADAY_MAX_AGE_MS;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-white px-3 py-2 ring-1 ring-slate-100">
      <div className="text-[10px] font-bold text-slate-500">{label}</div>
      <div className="mt-0.5 break-words text-sm font-semibold text-slate-800">{value}</div>
    </div>
  );
}

function MarketBreadthBar({ data }: { data: MarketBreadthSignal }) {
  const advance = data.advance_count ?? 0;
  const decline = data.decline_count ?? 0;
  const flat = data.flat_count ?? 0;
  const tradedTotal = data.traded_sample_count ?? advance + decline + flat;
  const advanceRatio =
    data.advance_ratio_percent ?? (tradedTotal > 0 ? (advance / tradedTotal) * 100 : 0);
  const declineRatio =
    data.decline_ratio_percent ?? (tradedTotal > 0 ? (decline / tradedTotal) * 100 : 0);
  const flatRatio = data.flat_ratio_percent ?? (tradedTotal > 0 ? (flat / tradedTotal) * 100 : 0);

  return (
    <div className="mt-4 min-w-0 rounded-2xl border border-slate-200 bg-[#fbfaf7] px-4 py-4">
      <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-3">
        <div className="text-emerald-700">
          <p className="text-[11px] font-bold tracking-wide">下跌</p>
          <p className="mt-0.5 font-serif text-2xl font-bold tabular-nums">{formatCount(decline)}</p>
          <p className="text-[11px] font-semibold tabular-nums">{declineRatio.toFixed(1)}%</p>
        </div>
        <div className="pb-1 text-center text-slate-500">
          <p className="text-[10px] font-bold uppercase tracking-[0.16em]">沪深个股</p>
          <p className="mt-1 text-xs font-semibold">{formatCount(tradedTotal)} 只交易样本</p>
        </div>
        <div className="text-right text-rose-700">
          <p className="text-[11px] font-bold tracking-wide">上涨</p>
          <p className="mt-0.5 font-serif text-2xl font-bold tabular-nums">{formatCount(advance)}</p>
          <p className="text-[11px] font-semibold tabular-nums">{advanceRatio.toFixed(1)}%</p>
        </div>
      </div>
      <div
        className="mt-3 flex h-4 overflow-hidden rounded-full bg-slate-100 ring-1 ring-slate-200"
        aria-label={`下跌${decline}只，平盘${flat}只，上涨${advance}只`}
      >
        <span className="bg-emerald-500" style={{ width: `${declineRatio}%` }} />
        <span className="bg-slate-300" style={{ width: `${flatRatio}%` }} />
        <span className="bg-rose-500" style={{ width: `${advanceRatio}%` }} />
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] font-semibold text-slate-500">
        <span>绿：下跌</span>
        <span>平盘 {formatCount(flat)} · {flatRatio.toFixed(1)}%</span>
        <span>红：上涨</span>
      </div>
    </div>
  );
}

/**
 * M1.1/M5：大盘情绪温度计。挂载在市场 Tab（全市场自上而下参考）与生成日报
 * 诊断区 `DiagnosticsAccordion`（辅助判断当日决策是否要更谨慎/更果断）。
 * 自行请求数据（对齐 SectorSignalBacktestPanel 的自包含模式），两处挂载点
 * 无需各自维护数据获取逻辑。
 */
export function MarketBreadthGauge({ compact = false }: MarketBreadthGaugeProps) {
  const { data, error, loading, revalidating, refresh } = useCachedFetch<MarketBreadthSignal>({
    cacheKey: MARKET_BREADTH_CACHE_KEY,
    fetcher: fetchMarketBreadth,
    staleTimeMs: MARKET_BREADTH_STALE_MS,
  });

  useEffect(() => {
    let timer: number | null = null;
    const stop = () => {
      if (timer != null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const start = () => {
      if (timer == null) {
        timer = window.setInterval(() => {
          void refresh();
        }, MARKET_BREADTH_REFRESH_MS);
      }
    };
    const handleVisibility = () => {
      if (document.hidden) {
        stop();
        return;
      }
      void refresh();
      start();
    };

    if (!document.hidden) {
      start();
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [refresh]);

  if (loading && !data) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">大盘情绪温度计</h3>
        <div className="mt-2 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          正在获取最新全市场情绪…
        </div>
      </section>
    );
  }

  if (!data || !data.available) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">大盘情绪温度计</h3>
        <p className="mt-2 text-sm text-slate-600" role="status">
          {data?.message ?? "情绪温度计暂不可用，不影响其余分析结果。"}
        </p>
        <p className="mt-2 text-xs font-semibold text-amber-700">当前不参与日报分析与守卫。</p>
      </section>
    );
  }

  const tone = resolveTone(data);
  const isIntraday = data.signal_mode === "intraday";
  const intradayExpired = isExpiredIntradaySnapshot(data);
  const backendStale = data.stale === true || data.freshness_status === "stale";
  const isStale = backendStale || intradayExpired;
  const sourceLabel = data.source_mode
    ? SOURCE_LABEL[data.source_mode]
    : isIntraday
      ? "盘中准实时"
      : "收盘历史口径";
  const decisionEligible = data.decision_eligible === true && !isStale;
  const decisionLabel = decisionEligible ? "数据可参与当前决策" : "数据仅展示，不参与当前决策";
  const decisionMessage = intradayExpired
    ? "盘中快照已超过10分钟未更新，客户端已停止将其用于决策。"
    : backendStale
      ? "数据已过有效期，守卫不会据此升级动作。"
      : data.decision_message ??
        data.decision_status ??
        "当前口径未被标记为可参与决策。";
  const changeText =
    data.sentiment_level_change != null && data.sentiment_level_change !== 0
      ? data.sentiment_level_change < 0
        ? `较上一交易日转冷 ${Math.abs(data.sentiment_level_change)} 档`
        : `较上一交易日转热 ${data.sentiment_level_change} 档`
      : null;

  return (
    <section
      className="glass-panel min-w-0 max-w-full overflow-hidden rounded-[24px] p-5"
      data-testid="market-breadth-gauge"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[var(--brand)] text-white">
            <Gauge size={20} aria-hidden />
          </div>
          <div className="min-w-0">
            <h3 className="text-lg font-black text-slate-950">沪深市场情绪</h3>
            <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-slate-600">
              <span className="font-semibold text-slate-700">{sourceLabel}</span>
              <span aria-hidden>·</span>
              {data.universe_scope ? (
                <>
                  <span>{data.universe_scope}</span>
                  <span aria-hidden>·</span>
                </>
              ) : null}
              <span>更新于 {formatAsOf(data.as_of_datetime, data.trade_date)}</span>
              {revalidating ? (
                <span className="inline-flex items-center gap-1 text-[var(--brand)]" role="status">
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />更新中
                </span>
              ) : null}
            </p>
          </div>
        </div>
        <StatusPill tone={tone}>{data.breadth_tone ?? data.sentiment_level ?? "—"}</StatusPill>
      </div>

      <div
        className={`mt-3 rounded-xl border px-3 py-2 ${
          decisionEligible
            ? "border-emerald-200 bg-emerald-50/80"
            : "border-amber-200 bg-amber-50/80"
        }`}
        data-testid="market-breadth-decision-status"
      >
        <div
          className={`text-xs font-bold ${decisionEligible ? "text-emerald-800" : "text-amber-800"}`}
        >
          {decisionLabel}
        </div>
        <p className="mt-0.5 text-xs leading-5 text-slate-600">{decisionMessage}</p>
      </div>

      {isStale ? (
        <p className="mt-2 text-xs font-semibold text-amber-700" role="status">
          数据已过期，继续展示上次有效快照，但不会用于动作升级。
        </p>
      ) : null}
      {error ? (
        <p className="mt-2 text-xs text-amber-700" role="status">
          本次更新失败，正在显示上次数据。
        </p>
      ) : null}

      {data.interpretation ? (
        <p className="mt-3 text-sm leading-6 text-slate-700">
          {data.interpretation}
          {changeText ? `（${changeText}）` : ""}
        </p>
      ) : null}

      {isIntraday ? (
        <>
          <MarketBreadthBar data={data} />
          <div className={`mt-3 grid gap-2 ${compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-4"}`}>
            <Metric label="全样本（含停牌）" value={formatCount(data.market_sample_count)} />
            <Metric label="停牌" value={formatCount(data.suspended_count)} />
            <Metric
              label="真实涨停"
              value={formatCount(data.real_limit_up_count ?? data.limit_up_count)}
            />
            <Metric
              label="真实跌停"
              value={formatCount(data.real_limit_down_count ?? data.limit_down_count)}
            />
          </div>
        </>
      ) : (
        <div className={`mt-4 grid gap-2 ${compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-4"}`}>
          <Metric label="涨停家数" value={formatCount(data.limit_up_count)} />
          <Metric label="跌停家数" value={formatCount(data.limit_down_count)} />
          <Metric label="炸板率" value={formatPercent(data.limit_up_broken_ratio_percent)} />
          <Metric label="最高连板" value={formatCount(data.max_consecutive_boards)} />
        </div>
      )}

      <details className="mt-3 rounded-xl border border-slate-200 bg-white/70 px-3 py-2">
        <summary className="cursor-pointer text-xs font-bold text-slate-700 marker:text-slate-400">
          收盘口径与辅助证据
        </summary>
        <div className="mt-2 text-xs leading-5 text-slate-600">
          <p>
            收盘锚点：{data.closing_trade_date ?? data.trade_date ?? "—"}
            {data.closing_sentiment_level || data.closing_breadth_percentile != null ? " · " : ""}
            {data.closing_sentiment_level ?? ""}
            {data.closing_breadth_percentile != null
              ? `（近2年分布第 ${data.closing_breadth_percentile.toFixed(1)} 百分位）`
              : ""}
          </p>
          {data.limit_up_broken_ratio_percent != null || data.max_consecutive_boards != null ? (
            <p>
              炸板率 {formatPercent(data.limit_up_broken_ratio_percent)} · 最高连板{" "}
              {formatCount(data.max_consecutive_boards)}
              {data.limit_pool_as_of_date ? `（截至 ${data.limit_pool_as_of_date}）` : ""}
            </p>
          ) : null}
          {data.margin_available ? (
            <p>
              两融余额环比{data.margin_scope === "sse_only" ? "（仅沪市）" : ""}{" "}
              {formatYi(data.margin_balance_change_yi)}
              {data.margin_as_of_date ? `（截至 ${data.margin_as_of_date}）` : ""}
            </p>
          ) : null}
        </div>
      </details>

      <p className="mt-3 text-xs leading-5 text-slate-500">
        乐咕活跃度包含停牌股分母；比例条仅比较实际交易的上涨、下跌与平盘样本。盘中每5分钟更新；过期或回退数据不参与强守卫。
      </p>

      {!compact ? <FundReturnDistributionPanel /> : null}
    </section>
  );
}
