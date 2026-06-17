import type { MarketSectorBoardList, MarketSectorBoardWidget } from "@/lib/api";

export function isMarketWidgetUsable(data: MarketSectorBoardWidget | null | undefined): boolean {
  if (!data?.available) {
    return false;
  }
  return (
    (data.top_gainers?.length ?? 0) > 0 ||
    (data.top_losers?.length ?? 0) > 0 ||
    (data.top_inflow?.length ?? 0) > 0 ||
    (data.top_outflow?.length ?? 0) > 0
  );
}

export function isMarketListUsable(data: MarketSectorBoardList | null | undefined): boolean {
  return Boolean(data?.available && (data.items?.length ?? 0) > 0);
}

export function acceptMarketWidgetFresh(fresh: MarketSectorBoardWidget): boolean {
  return isMarketWidgetUsable(fresh);
}

export function acceptMarketListFresh(fresh: MarketSectorBoardList): boolean {
  return isMarketListUsable(fresh);
}
