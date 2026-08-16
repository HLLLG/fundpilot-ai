"use client";

import { Crosshair } from "lucide-react";

/**
 * 荐基决策策略已收敛为单一的「机会优先」口径，不再提供「稳健筛选」分支，
 * 所以这里只是把当前生效的策略讲清楚，没有可切换的选项。
 * 历史报告仍可能记录 risk_first，回显文案在 DiscoveryReportPanel 内保留。
 */
export function DiscoveryStrategySelector() {
  return (
    <fieldset aria-label="荐基决策策略">
      <legend className="sr-only">荐基决策策略</legend>
      <div
        data-testid="discovery-strategy-opportunity_first"
        className="min-h-[92px] rounded-xl border border-[var(--brand)] bg-[var(--brand-soft)] px-3.5 py-3 text-left shadow-[inset_3px_0_0_var(--brand)]"
      >
        <span className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-sm font-black text-slate-950">
            <Crosshair size={17} aria-hidden="true" className="text-[var(--brand-strong)]" />
            机会优先
          </span>
          <span className="rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-black text-[var(--brand-strong)] ring-1 ring-[var(--brand)]/20">
            高弹性
          </span>
        </span>
        <span className="mt-2 block text-[11px] font-semibold leading-5 text-slate-600">
          优先高波动、高动量与回撤修复机会；质量只作准入，不奖励低波动。
        </span>
      </div>
    </fieldset>
  );
}
