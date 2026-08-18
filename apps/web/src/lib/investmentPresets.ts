import type { InvestorProfile } from "@/lib/api";

export function takeProfitThresholdPercent(profile: InvestorProfile): number {
  const fee = profile.round_trip_fee_percent ?? 1.5;
  const net = profile.min_net_profit_percent ?? 1.0;
  return Math.round((fee + net) * 100) / 100;
}
