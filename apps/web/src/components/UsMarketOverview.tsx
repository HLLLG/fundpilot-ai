"use client";

import { Loader2 } from "lucide-react";
import type {
  UsDataSourceStatus,
  UsFuturesQuote,
  UsMarketSnapshot,
  UsSessionKind,
  UsdCnyQuote,
} from "@/lib/api";
import { US_SESSION_LABEL } from "@/lib/usMarketOverview";

type UsMarketOverviewProps = {
  data: UsMarketSnapshot | null;
  loading: boolean;
  revalidating?: boolean;
};

const A_SHARE_CONTEXT_HINT =
  "隔夜美股指数涨跌可作为 A 股下一交易日情绪参考（科技板块多看纳斯达克，整体风险偏好多看标普500），不代表 A 股必然同向涨跌。";

function metricCaliberLabel(
  caliber: string | null | undefined,
  sessionKind: UsSessionKind,
): string | null {
  if (!caliber) {
    return sessionKind === "pre_market" || sessionKind === "regular" ? "期货" : null;
  }
  if (caliber === "futures_live" || caliber === "futures_night") {
    return "期货";
  }
  if (caliber === "index_close") {
    return "收盘";
  }
  return null;
}

function formatPercent(value: number | null | undefined) {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

function formatPrice(value: number | null | undefined) {
  if (value == null) {
    return "—";
  }
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function profitClass(value: number | null | undefined) {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

function tileTone(value: number | null | undefined, status: UsDataSourceStatus) {
  if (status === "unavailable" || value == null || value === 0) {
    return "bg-slate-100";
  }
  return value > 0 ? "bg-rose-50" : "bg-emerald-50";
}

function formatClock(value: string | null | undefined) {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type MetricCardProps = {
  name: string;
  lastPrice?: number | null;
  changePercent?: number | null;
  quoteTime?: string | null;
  caliberLabel?: string | null;
  status: UsDataSourceStatus;
};

function MetricCard({
  name,
  lastPrice,
  changePercent,
  quoteTime,
  caliberLabel,
  status,
}: MetricCardProps) {
  const unavailable = status === "unavailable";
  const stale = status === "stale";

  return (
    <div className={`rounded-xl px-3 py-3 ${tileTone(changePercent, status)}`}>
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-xs text-slate-600">{name}</span>
        <div className="flex shrink-0 items-center gap-1">
          {caliberLabel ? (
            <span className="rounded bg-slate-200/80 px-1.5 py-0.5 text-[10px] text-slate-500">
              {caliberLabel}
            </span>
          ) : null}
          {stale && quoteTime ? (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700">
              上次 {formatClock(quoteTime)}
            </span>
          ) : null}
        </div>
      </div>

      {unavailable ? (
        <div className="mt-2 text-sm font-medium text-slate-400">暂不可用</div>
      ) : (
        <div className="mt-1.5">
          <div className="text-base font-semibold tabular-nums text-slate-900">
            {formatPrice(lastPrice)}
          </div>
          <div className={`text-sm font-semibold tabular-nums ${profitClass(changePercent)}`}>
            {formatPercent(changePercent)}
          </div>
        </div>
      )}
    </div>
  );
}

function renderFuturesCards(futures: UsFuturesQuote[], sessionKind: UsSessionKind) {
  return futures.map((quote) => (
    <MetricCard
      key={quote.symbol}
      name={quote.display_name}
      lastPrice={quote.last_price}
      changePercent={quote.change_percent}
      quoteTime={quote.quote_time}
      caliberLabel={metricCaliberLabel(quote.quote_caliber, sessionKind)}
      status={quote.status}
    />
  ));
}

function renderUsdCnyCard(usdCny: UsdCnyQuote) {
  return (
    <MetricCard
      key="USD_CNY"
      name="汇率"
      lastPrice={usdCny.last_price}
      changePercent={usdCny.change_percent}
      quoteTime={usdCny.quote_time}
      status={usdCny.status}
    />
  );
}

export function UsMarketOverview({ data, loading, revalidating = false }: UsMarketOverviewProps) {
  if (loading && !data) {
    return (
      <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
        <div className="flex items-center justify-center py-12 text-sm text-slate-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          加载美股概览…
        </div>
      </section>
    );
  }

  if (!data) {
    return (
      <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
        <p className="py-12 text-center text-sm text-slate-500">美股概览暂不可用</p>
      </section>
    );
  }

  const sessionLabel = US_SESSION_LABEL[data.session_kind] ?? data.session_label;
  const updatedAt = formatClock(data.updated_at);

  return (
    <section className="grid gap-4">
      <div className="flex items-center justify-between">
        <div className="inline-flex items-center gap-2">
          <span className="inline-flex items-center rounded-full bg-slate-900 px-3 py-1 text-xs font-medium text-white">
            美股 · {sessionLabel}
          </span>
          {data.et_date ? <span className="text-xs text-slate-400">{data.et_date} ET</span> : null}
        </div>
        {revalidating ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" aria-hidden />
        ) : null}
      </div>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {renderFuturesCards(data.futures, data.session_kind)}
          {renderUsdCnyCard(data.usd_cny)}
        </div>
        <p className="mt-3 text-xs leading-relaxed text-slate-400">{A_SHARE_CONTEXT_HINT}</p>
      </div>

      {updatedAt ? (
        <p className="text-center text-xs text-slate-400">
          更新时间 {updatedAt}
          {data.stale ? " · 上次缓存" : data.from_cache ? " · 缓存" : ""}
        </p>
      ) : null}
    </section>
  );
}
