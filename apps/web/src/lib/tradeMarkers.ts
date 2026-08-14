import type { FundTransaction } from "@/lib/api";

export type TradeMarkerKind = "buy" | "sell";

export type TradeMarker = {
  date: string;
  kind: TradeMarkerKind;
  pending: boolean;
  items: {
    direction: TradeMarkerKind;
    amount_yuan: number;
    trade_time: string;
    status: string;
  }[];
};

export function buildTradeMarkers(transactions: FundTransaction[]): TradeMarker[] {
  const groups = new Map<string, TradeMarker>();
  for (const tx of transactions) {
    if (tx.status === "skipped" || tx.status === "superseded") {
      continue;
    }
    const date = tx.confirm_date?.slice(0, 10);
    if (!date) {
      continue;
    }
    const kind: TradeMarkerKind = tx.direction;
    const key = `${date}|${kind}`;
    const item = {
      direction: tx.direction,
      amount_yuan: tx.amount_yuan,
      trade_time: tx.trade_time,
      status: tx.status,
    };
    const existing = groups.get(key);
    if (existing) {
      existing.items.push(item);
      if (tx.status === "pending") {
        existing.pending = true;
      }
      continue;
    }
    groups.set(key, {
      date,
      kind,
      pending: tx.status === "pending",
      items: [item],
    });
  }
  return [...groups.values()].sort((left, right) => {
    const byDate = left.date.localeCompare(right.date);
    if (byDate !== 0) {
      return byDate;
    }
    return left.kind.localeCompare(right.kind);
  });
}
