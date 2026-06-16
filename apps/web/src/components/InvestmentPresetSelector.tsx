"use client";

import type { InvestmentPreset, InvestorProfile } from "@/lib/api";
import {
  PRESET_OPTIONS,
  applyInvestmentPreset,
  takeProfitThresholdPercent,
} from "@/lib/investmentPresets";

type InvestmentPresetSelectorProps = {
  profile: InvestorProfile;
  onChange: (profile: InvestorProfile) => void;
  compact?: boolean;
};

export function InvestmentPresetSelector({
  profile,
  onChange,
  compact = false,
}: InvestmentPresetSelectorProps) {
  const preset = profile.investment_preset ?? "conservative_hold";
  const threshold =
    preset === "aggressive_swing" ? takeProfitThresholdPercent(profile) : null;

  return (
    <div className={compact ? "space-y-2" : "space-y-3"}>
      <div className="grid grid-cols-2 gap-2">
        {PRESET_OPTIONS.map((option) => (
          <button
            key={option.id}
            type="button"
            data-testid={`investment-preset-${option.id}`}
            onClick={() => onChange(applyInvestmentPreset(option.id, profile))}
            className={`rounded-xl border px-3 py-2.5 text-left transition ${
              preset === option.id
                ? option.id === "aggressive_swing"
                  ? "border-rose-300 bg-rose-50 text-rose-950"
                  : "border-emerald-300 bg-emerald-50 text-emerald-950"
                : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            <div className="text-xs font-black">{option.label}</div>
            <div className="mt-0.5 text-[10px] font-semibold leading-4 opacity-80">
              {option.hint}
            </div>
          </button>
        ))}
      </div>
      {threshold != null ? (
        <p className="rounded-lg border border-rose-100 bg-rose-50/80 px-2.5 py-2 text-[11px] font-semibold leading-5 text-rose-900">
          扣费止盈线约 <span className="font-black">{threshold}%</span>
          （手续费 {profile.round_trip_fee_percent ?? 1.5}% + 净赚{" "}
          {profile.min_net_profit_percent ?? 1}%）；目标持有{" "}
          {profile.hold_days_target ?? 7} 天内。
        </p>
      ) : null}
    </div>
  );
}
