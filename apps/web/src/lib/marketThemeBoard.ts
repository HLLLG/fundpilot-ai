import type { MarketThemeBoardResponse } from "@/lib/api";

export function isMarketThemeBoardUsable(data: MarketThemeBoardResponse | null | undefined): boolean {
  return Boolean(data?.available && (data.items?.length ?? 0) > 0);
}

export function acceptMarketThemeBoardFresh(fresh: MarketThemeBoardResponse): boolean {
  return isMarketThemeBoardUsable(fresh);
}

export function themeBoardHeading(): string {
  return "今日板块涨幅榜";
}

export function formatThemeBoardUpdatedAt(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `更新于 ${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function formatThemeRank(rank: number | undefined, index: number): string {
  const value = rank ?? index + 1;
  return String(value).padStart(2, "0");
}

export function themeRankClass(rank: number | undefined, index: number): string {
  const value = rank ?? index + 1;
  return value <= 3 ? "font-semibold text-amber-500" : "font-medium text-slate-500";
}

export function formatThemePercent(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

export function formatConsecutiveDays(value: number | null | undefined): string {
  if (value == null || value <= 0) {
    return "—";
  }
  return `+${value}天`;
}

export function profitToneClass(value: number | null | undefined): string {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

const SUB_TAB_STORAGE_KEY = "fundpilot-market-sub-tab";

export type MarketSubTab = "market" | "themes" | "us";

export function loadMarketSubTab(): MarketSubTab {
  if (typeof window === "undefined") {
    return "market";
  }
  const stored = window.sessionStorage.getItem(SUB_TAB_STORAGE_KEY);
  return stored === "themes" || stored === "us" ? stored : "market";
}

export function saveMarketSubTab(tab: MarketSubTab): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(SUB_TAB_STORAGE_KEY, tab);
}
