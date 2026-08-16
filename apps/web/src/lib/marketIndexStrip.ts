import type { CnIndexQuote, UsDataSourceStatus, UsFuturesQuote } from "@/lib/api";

export type IndexStripCard = {
  key: string;
  name: string;
  lastPrice: number | null;
  change: number | null;
  changePercent: number | null;
  status: UsDataSourceStatus;
  priceDigits: number;
  changeDigits: number;
};

export function changeFromPriceAndPercent(
  price: number | null | undefined,
  percent: number | null | undefined,
): number | null {
  if (price == null || percent == null) {
    return null;
  }
  const denom = 1 + percent / 100;
  if (denom === 0) {
    return null;
  }
  return price - price / denom;
}

export function toCnIndexCards(items: CnIndexQuote[] | null | undefined): IndexStripCard[] {
  return (items ?? []).map((item) => ({
    key: item.symbol,
    name: item.display_name,
    lastPrice: item.last_price ?? null,
    change: item.change ?? changeFromPriceAndPercent(item.last_price, item.change_percent),
    changePercent: item.change_percent ?? null,
    status: item.status,
    priceDigits: 2,
    changeDigits: 2,
  }));
}

export function toUsIndexCards(futures: UsFuturesQuote[] | null | undefined): IndexStripCard[] {
  return (futures ?? []).map((quote) => ({
    key: quote.symbol,
    name: quote.display_name,
    lastPrice: quote.last_price ?? null,
    change: changeFromPriceAndPercent(quote.last_price, quote.change_percent),
    changePercent: quote.change_percent ?? null,
    status: quote.status,
    priceDigits: 2,
    changeDigits: 2,
  }));
}

export function formatIndexPrice(value: number | null | undefined, digits = 2): string {
  if (value == null) {
    return "—";
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatIndexChange(value: number | null | undefined, digits = 2): string {
  if (value == null) {
    return "—";
  }
  const rounded = Number(value.toFixed(digits));
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded.toFixed(digits)}`;
}

export function formatIndexPercent(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

export function indexTone(value: number | null | undefined, status: UsDataSourceStatus): "up" | "down" | "flat" {
  if (status === "unavailable" || value == null || value === 0) {
    return "flat";
  }
  return value > 0 ? "up" : "down";
}
