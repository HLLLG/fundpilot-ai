import type { MarketThemeBoardItem, MarketThemeBoardResponse } from "@/lib/api";

export type ThemeSortColumn = "change" | "change5d" | "inflow";
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

export function formatThemeBoardUpdatedAt(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `更新于 ${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function formatThemeBoardUpdatedFromIso(iso: string | null | undefined): string {
  if (!iso) {
    return "加载中…";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "加载中…";
  }
  return formatThemeBoardUpdatedAt(date);
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
