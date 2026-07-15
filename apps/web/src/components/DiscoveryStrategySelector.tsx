"use client";

import { Crosshair, ShieldCheck } from "lucide-react";
import type { DiscoveryStrategy } from "@/lib/api";

type DiscoveryStrategySelectorProps = {
  value: DiscoveryStrategy;
  onChange: (value: DiscoveryStrategy) => void;
};

const OPTIONS: Array<{
  id: DiscoveryStrategy;
  label: string;
  badge: string;
  description: string;
  icon: typeof Crosshair;
}> = [
  {
    id: "opportunity_first",
    label: "机会优先",
    badge: "推荐",
    description: "看未来 20～60 个交易日；历史回撤影响首批仓位，不单独否决机会。",
    icon: Crosshair,
  },
  {
    id: "risk_first",
    label: "稳健筛选",
    badge: "低波动",
    description: "沿用严格历史波动与量化覆盖门槛，更容易只保留观察候选。",
    icon: ShieldCheck,
  },
];

export function DiscoveryStrategySelector({
  value,
  onChange,
}: DiscoveryStrategySelectorProps) {
  return (
    <fieldset aria-label="荐基决策策略">
      <legend className="sr-only">荐基决策策略</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {OPTIONS.map((option) => {
          const selected = value === option.id;
          const Icon = option.icon;
          return (
            <button
              key={option.id}
              type="button"
              data-testid={`discovery-strategy-${option.id}`}
              aria-pressed={selected}
              onClick={() => onChange(option.id)}
              className={`group min-h-[92px] rounded-xl border px-3.5 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2 ${
                selected
                  ? "border-[var(--brand)] bg-[var(--brand-soft)] shadow-[inset_3px_0_0_var(--brand)]"
                  : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
              }`}
            >
              <span className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-sm font-black text-slate-950">
                  <Icon
                    size={17}
                    aria-hidden="true"
                    className={selected ? "text-[var(--brand-strong)]" : "text-slate-500"}
                  />
                  {option.label}
                </span>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
                  selected
                    ? "bg-white/80 text-[var(--brand-strong)] ring-1 ring-[var(--brand)]/20"
                    : "bg-slate-100 text-slate-500"
                }`}>
                  {option.badge}
                </span>
              </span>
              <span className="mt-2 block text-[11px] font-semibold leading-5 text-slate-600">
                {option.description}
              </span>
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-slate-500">
        账户亏损复核线仍用于日报；荐基只用候选历史波动调整首批金额与风险提示。
      </p>
    </fieldset>
  );
}
