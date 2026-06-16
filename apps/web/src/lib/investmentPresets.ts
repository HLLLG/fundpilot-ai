import type { InvestorProfile, InvestmentPreset, SwingMonitorScope } from "@/lib/api";

export function takeProfitThresholdPercent(profile: InvestorProfile): number {
  const fee = profile.round_trip_fee_percent ?? 1.5;
  const net = profile.min_net_profit_percent ?? 1.0;
  return Math.round((fee + net) * 100) / 100;
}

export function applyInvestmentPreset(
  preset: InvestmentPreset,
  profile: InvestorProfile,
): InvestorProfile {
  if (preset === "conservative_hold") {
    return {
      ...profile,
      investment_preset: preset,
      style: "稳健",
      horizon: "半年到一年",
      decision_style: "conservative",
      prefer_dca: true,
      avoid_chasing: true,
      max_drawdown_percent: 8,
      concentration_limit_percent: 35,
      swing_alerts_enabled: false,
      swing_monitor_scope: "both",
    };
  }
  return {
    ...profile,
    investment_preset: preset,
    style: "激进",
    horizon: "3-7天",
    decision_style: "aggressive",
    prefer_dca: false,
    avoid_chasing: false,
    max_drawdown_percent: 12,
    concentration_limit_percent: 40,
    round_trip_fee_percent: profile.round_trip_fee_percent ?? 1.5,
    min_net_profit_percent: profile.min_net_profit_percent ?? 1.0,
    hold_days_target: profile.hold_days_target ?? 7,
    swing_alerts_enabled: true,
    swing_monitor_scope: "both" as SwingMonitorScope,
  };
}

export const PRESET_OPTIONS: Array<{
  id: InvestmentPreset;
  label: string;
  hint: string;
}> = [
  {
    id: "conservative_hold",
    label: "稳健持有",
    hint: "半年～一年，偏定投、拒绝追高",
  },
  {
    id: "aggressive_swing",
    label: "激进波段",
    hint: "3～7 天，跌买涨卖，扣费后止盈",
  },
];
