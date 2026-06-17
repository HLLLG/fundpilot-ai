import type { MarketThemeBoardResponse } from "@/lib/api";

export function isMarketThemeBoardUsable(data: MarketThemeBoardResponse | null | undefined): boolean {
  return Boolean(data?.available && (data.items?.length ?? 0) > 0);
}

export function acceptMarketThemeBoardFresh(fresh: MarketThemeBoardResponse): boolean {
  return isMarketThemeBoardUsable(fresh);
}

const SUB_TAB_STORAGE_KEY = "fundpilot-market-sub-tab";

export type MarketSubTab = "market" | "themes";

export function loadMarketSubTab(): MarketSubTab {
  if (typeof window === "undefined") {
    return "market";
  }
  const stored = window.sessionStorage.getItem(SUB_TAB_STORAGE_KEY);
  return stored === "themes" ? "themes" : "market";
}

export function saveMarketSubTab(tab: MarketSubTab): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(SUB_TAB_STORAGE_KEY, tab);
}
