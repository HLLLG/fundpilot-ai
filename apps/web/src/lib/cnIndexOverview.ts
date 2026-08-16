import type { CnIndexOverview, CnIndexQuote, IndexDailyPoint } from "@/lib/api";

/** 页面停留时向服务器重读的间隔，与后台 A 股盘中刷新（20min）对齐。 */
export const CN_INDEX_REFRESH_INTERVAL_MS = 1_200_000;

const CN_INDEX_LIVE_SESSIONS = new Set(["trading_day_intraday", "trading_day_pre_close"]);

/** 盘中才轮询；周末 / 盘前 / 收盘后返回 null，等开盘再拉。 */
export function cnIndexRefreshIntervalMs(sessionKind?: string | null): number | null {
  return sessionKind && CN_INDEX_LIVE_SESSIONS.has(sessionKind)
    ? CN_INDEX_REFRESH_INTERVAL_MS
    : null;
}

/** 与美股「盘中 / 休市」同风格的短标签。 */
export const CN_SESSION_LABEL: Record<string, string> = {
  trading_day_pre_open: "开盘前",
  trading_day_intraday: "盘中",
  trading_day_pre_close: "盘中",
  trading_day_after_close: "已收盘",
  non_trading_day: "休市",
};

export function formatCnIndexCaption(data: {
  session_kind?: string | null;
  trade_date?: string | null;
} | null | undefined): string | null {
  if (!data) {
    return null;
  }
  const sessionLabel = data.session_kind
    ? (CN_SESSION_LABEL[data.session_kind] ?? null)
    : null;
  return [sessionLabel, data.trade_date].filter(Boolean).join(" · ") || null;
}

export const CN_INDEX_SPECS = [
  { symbol: "000001", name: "上证指数" },
  { symbol: "399001", name: "深证成指" },
  { symbol: "399006", name: "创业板指" },
  { symbol: "000300", name: "沪深300" },
  { symbol: "000688", name: "科创50" },
] as const;

export function isCnIndexOverviewUsable(data: CnIndexOverview | null | undefined): boolean {
  return Boolean(data?.available && (data.items?.length ?? 0) > 0);
}

export function acceptCnIndexFresh(fresh: CnIndexOverview): boolean {
  return isCnIndexOverviewUsable(fresh);
}

export function quoteFromDailyPoints(
  symbol: string,
  displayName: string,
  points: IndexDailyPoint[] | null | undefined,
): CnIndexQuote {
  const series = points ?? [];
  const last = series[series.length - 1];
  const prev = series[series.length - 2];
  if (!last) {
    return {
      symbol,
      display_name: displayName,
      last_price: null,
      change: null,
      change_percent: null,
      quote_time: null,
      status: "unavailable",
    };
  }
  const lastPrice = last.close;
  const prevClose = prev?.close;
  const change = prevClose == null ? null : lastPrice - prevClose;
  const changePercent =
    prevClose == null || prevClose === 0 ? null : ((lastPrice / prevClose) - 1) * 100;
  return {
    symbol,
    display_name: displayName,
    last_price: lastPrice,
    change,
    change_percent: changePercent,
    quote_time: last.date,
    status: "ok",
  };
}

export function overviewFromDailyRows(
  rows: Array<{ symbol: string; name: string; points: IndexDailyPoint[] }>,
): CnIndexOverview {
  const items = rows.map((row) => quoteFromDailyPoints(row.symbol, row.name, row.points));
  return {
    items,
    available: items.some((item) => item.status !== "unavailable"),
    from_cache: false,
    stale: false,
    updated_at: new Date().toISOString(),
    trade_date: items.find((item) => item.quote_time)?.quote_time ?? null,
    message: null,
  };
}

export function parseApiErrorDetail(raw: string, fallback = "行情数据加载失败"): string {
  const text = raw.trim();
  if (!text) {
    return fallback;
  }
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail.trim() === "Not Found" ? "主要指数接口暂不可用" : body.detail.trim();
    }
  } catch {
    // 非 JSON 时原样返回，避免丢掉后端已写好的中文。
  }
  return text;
}
