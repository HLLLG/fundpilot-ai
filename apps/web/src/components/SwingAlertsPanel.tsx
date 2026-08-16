"use client";

import { Bell, Loader2, RefreshCw } from "lucide-react";
import type { SwingAlertItem } from "@/lib/api";

const TYPE_LABEL: Record<SwingAlertItem["alert_type"], string> = {
  take_profit: "止盈",
  dip_buy: "跌买",
  pullback: "回吐",
  sector_dip: "板块",
};

type SwingAlertsPanelProps = {
  items: SwingAlertItem[];
  sessionKind: string | null;
  isEvaluating: boolean;
  error: string | null;
  onRefresh: () => void;
};

export function SwingAlertsPanel({
  items,
  sessionKind,
  isEvaluating,
  error,
  onRefresh,
}: SwingAlertsPanelProps) {
  if (sessionKind && !["trading_day_intraday", "trading_day_pre_close"].includes(sessionKind)) {
    return null;
  }

  return (
    <section className="mb-4 overflow-hidden rounded-[var(--radius-card)] border border-[var(--line)] bg-[var(--panel)]">
      <div className="market-board-head flex items-center justify-between gap-2 border-b border-[var(--line)] px-4 py-3">
        <div className="flex items-center gap-2">
          <Bell size={16} className="text-[var(--danger-icon)]" />
          <h3 className="text-sm font-black text-[var(--brand-deep)]">今日波段信号</h3>
          {isEvaluating ? <Loader2 size={14} className="animate-spin text-[var(--muted)]" /> : null}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-[var(--line)] px-2 py-2 text-[11px] font-bold text-[var(--muted)] hover:bg-[var(--surface-muted)]"
        >
          <RefreshCw size={12} />
          刷新
        </button>
      </div>
      {error ? (
        <p role="alert" className="px-4 py-3 text-xs font-semibold text-[var(--danger-fg)]">
          {error}
        </p>
      ) : items.length === 0 ? (
        <p className="px-4 py-3 text-xs leading-5 text-[var(--muted)]">
          暂无触发信号；每 15 分钟自动评估一次（评估前会刷新板块行情）。
        </p>
      ) : (
        <ul className="divide-y divide-[var(--line)]">
          {items.map((item) => (
            <li key={item.alert_key} className="px-4 py-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-bold text-[var(--brand-deep)]">{item.title}</p>
                  <p className="mt-1 text-xs leading-5 text-[var(--muted)]">{item.message}</p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-black ${
                    item.priority === "high"
                      ? "bg-[var(--danger-bg)] text-[var(--danger-fg)]"
                      : "bg-[var(--warn-bg)] text-[var(--warn-fg)]"
                  }`}
                >
                  {TYPE_LABEL[item.alert_type]}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
