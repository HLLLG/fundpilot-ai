import type { MarketThemeBoardItem, MarketThemeBoardResponse } from "@/lib/api";

export type ThemeSortColumn = "change" | "change5d" | "inflow" | "streak";
export type ThemeSortDirection = "asc" | "desc";

export function isMarketThemeBoardUsable(data: MarketThemeBoardResponse | null | undefined): boolean {
  return Boolean(data?.available && (data.items?.length ?? 0) > 0);
}

export function acceptMarketThemeBoardFresh(fresh: MarketThemeBoardResponse): boolean {
  return isMarketThemeBoardUsable(fresh);
}

export function themeBoardHeading(): string {
  return "主题板块涨跌";
}

const SHANGHAI_DATE_TIME = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const THEME_LIVE_SESSIONS = new Set(["trading_day_intraday", "trading_day_pre_close"]);

export function formatThemeBoardUpdatedAt(
  data: Pick<MarketThemeBoardResponse, "refreshed_at" | "trade_date" | "session_kind"> | null | undefined,
): string | null {
  const tradeDate = String(data?.trade_date || "").slice(0, 10);
  const live = THEME_LIVE_SESSIONS.has(String(data?.session_kind || ""));
  if (!live && /^\d{4}-\d{2}-\d{2}$/.test(tradeDate)) {
    return `更新：${tradeDate} 15:00`;
  }
  const refreshed = String(data?.refreshed_at || "").trim();
  if (refreshed) {
    const ms = Date.parse(refreshed);
    if (Number.isFinite(ms)) {
      const parts = SHANGHAI_DATE_TIME.formatToParts(new Date(ms));
      const pick = (type: Intl.DateTimeFormatPartTypes) =>
        parts.find((part) => part.type === type)?.value ?? "";
      return `更新：${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}`;
    }
  }
  return /^\d{4}-\d{2}-\d{2}$/.test(tradeDate) ? `更新：${tradeDate}` : null;
}

export function formatBoardKindLabel(kind: string | null | undefined): string {
  if (kind === "industry") return "行业";
  if (kind === "index") return "指数";
  return "概念";
}

export function boardKindClass(kind: string | null | undefined): string {
  if (kind === "industry") return "bg-slate-100 text-slate-600";
  if (kind === "index") return "bg-[var(--brand-soft)] text-[var(--brand-strong)]";
  return "bg-amber-100 text-amber-700";
}

export function formatThemeRank(rank: number | undefined, index: number): string {
  const value = rank ?? index + 1;
  return String(value).padStart(2, "0");
}

export function themeRankClass(rank: number | undefined, index: number): string {
  const value = rank ?? index + 1;
  return value <= 3 ? "font-semibold text-amber-700" : "font-medium text-slate-500";
}

export function formatThemePercent(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

export function profitToneClass(value: number | null | undefined): string {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

export function formatThemeStreak(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  const days = Math.round(value);
  if (days === 0) {
    return "0天";
  }
  return `${days > 0 ? "+" : ""}${days}天`;
}

export function formatThemeFlowYi(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}亿`;
}

export function hasThemeFlowDetail(item: {
  main_force_net_yi?: number | null;
  flow_tiers?: { super_large_net_yi?: number | null } | null;
}): boolean {
  return item.main_force_net_yi != null || item.flow_tiers != null;
}

export const THEME_FLOW_TIER_ROWS = [
  { key: "super_large_net_yi" as const, label: "超大单", hint: "机构" },
  { key: "large_net_yi" as const, label: "大单", hint: null },
  { key: "medium_net_yi" as const, label: "中单", hint: "大户" },
  { key: "small_net_yi" as const, label: "小单", hint: "散户" },
] as const;

export function sortThemeBoardItems(
  items: MarketThemeBoardItem[],
  column: ThemeSortColumn,
  direction: ThemeSortDirection,
): MarketThemeBoardItem[] {
  const key =
    column === "change"
      ? "change_1d_percent"
      : column === "change5d"
        ? "change_5d_percent"
        : column === "streak"
          ? "consecutive_up_days"
          : "main_force_net_yi";
  const sorted = [...items].sort((left, right) => {
    const leftValue = left[key];
    const rightValue = right[key];
    if (leftValue == null && rightValue == null) {
      return 0;
    }
    if (leftValue == null) {
      return 1;
    }
    if (rightValue == null) {
      return -1;
    }
    return direction === "desc" ? rightValue - leftValue : leftValue - rightValue;
  });
  return sorted.map((item, index) => ({ ...item, rank: index + 1 }));
}

export function normalizeThemeSearchText(value: string | null | undefined): string {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "");
}

export function themeBoardMatchesQuery(
  item: Pick<MarketThemeBoardItem, "sector_label" | "board_kind">,
  query: string,
): boolean {
  const needle = normalizeThemeSearchText(query);
  if (!needle) {
    return true;
  }
  if (normalizeThemeSearchText(item.sector_label).includes(needle)) {
    return true;
  }
  return normalizeThemeSearchText(formatBoardKindLabel(item.board_kind)).includes(needle);
}

export function filterThemeBoardItems(
  items: MarketThemeBoardItem[],
  options: { query?: string; heldOnly?: boolean } = {},
): MarketThemeBoardItem[] {
  const query = options.query ?? "";
  const heldOnly = Boolean(options.heldOnly);
  return items.filter((item) => {
    if (heldOnly && !item.in_portfolio) {
      return false;
    }
    return themeBoardMatchesQuery(item, query);
  });
}

export function countHeldThemeBoards(items: MarketThemeBoardItem[]): number {
  return items.reduce((count, item) => count + (item.in_portfolio ? 1 : 0), 0);
}

export function nextThemeSortState(
  column: ThemeSortColumn,
  activeColumn: ThemeSortColumn,
  direction: ThemeSortDirection,
): { column: ThemeSortColumn; direction: ThemeSortDirection } {
  if (column === activeColumn) {
    return { column, direction: direction === "desc" ? "asc" : "desc" };
  }
  return { column, direction: "desc" };
}

const SUB_TAB_STORAGE_KEY = "fundpilot-market-sub-tab";

export type MarketSubTab = "themes" | "us";

export function loadMarketSubTab(): MarketSubTab {
  if (typeof window === "undefined") {
    return "themes";
  }
  const stored = window.sessionStorage.getItem(SUB_TAB_STORAGE_KEY);
  if (stored === "us") {
    return stored;
  }
  return "themes";
}

export function saveMarketSubTab(tab: MarketSubTab): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(SUB_TAB_STORAGE_KEY, tab);
}
