"use client";

import type { SectorSignalBacktestRule } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

type ReportSignalBacktestPanelProps = {
  facts: Record<string, unknown> | undefined;
};

export function ReportSignalBacktestPanel({ facts }: ReportSignalBacktestPanelProps) {
  if (!facts) {
    return null;
  }

  const backtest = facts.signal_backtest as Record<string, unknown> | undefined;
  const guardPolicy = facts.guard_policy as Record<string, unknown> | undefined;

  if (!backtest?.has_data && !guardPolicy?.reason) {
    return null;
  }

  const byRule = (backtest?.by_rule as Record<string, SectorSignalBacktestRule>) ?? {};
  const rules = Object.values(byRule);

  return (
    <div className="mb-5 rounded-[24px] border border-indigo-100 bg-indigo-50/50 p-5">
      <div className="mb-3 text-sm font-black text-slate-950">板块信号回测（生成日报时快照）</div>
      {guardPolicy?.reason ? (
        <p className="mb-3 text-sm leading-6 text-indigo-950">{String(guardPolicy.reason)}</p>
      ) : null}
      {rules.length ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {rules.map((rule) => (
            <div key={rule.rule_id} className="rounded-2xl bg-white px-4 py-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold text-slate-900">{rule.label}</span>
                {rule.hit_rate_percent != null ? (
                  <StatusPill tone={rule.hit_rate_percent >= 53 ? "green" : "amber"}>
                    {rule.hit_rate_percent}%
                  </StatusPill>
                ) : null}
              </div>
              <p className="mt-1 text-xs text-slate-600">
                触发 {rule.trigger_count} 次 · 命中 {rule.hit_count}
              </p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
