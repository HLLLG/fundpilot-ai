"use client";

import { useEffect, useState } from "react";
import { BarChart3, Loader2 } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import { StatusPill } from "@/components/StatusPill";
import {
  DecisionMetricGrid,
  FeeBenchmarkMethodNote,
  LegacyReferenceStrip,
} from "@/components/DecisionMetricGrid";
import type { DiscoveryOutcomeItem, DiscoveryOutcomesPayload } from "@/lib/api";
import { fetchDiscoveryOutcomes } from "@/lib/api";

type DiscoveryOutcomesPanelProps = { reportId: string };
const HORIZONS = [5, 20, 60] as const;

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function signedPercent(value: number) {
  return `${value > 0 ? "+" : ""}${value}%`;
}

function itemStatus(item: DiscoveryOutcomeItem) {
  if (item.status === "hit") return { label: "正收益", tone: "green" as const };
  if (item.status === "miss") return { label: "未取得正收益", tone: "red" as const };
  if (item.status === "pending") return { label: "待成熟", tone: "amber" as const };
  return { label: "不进分母", tone: "blue" as const };
}

export function DiscoveryOutcomesPanel({ reportId }: DiscoveryOutcomesPanelProps) {
  const [horizon, setHorizon] = useState<number>(5);
  const [result, setResult] = useState<{
    reportId: string;
    horizon: number;
    data: DiscoveryOutcomesPayload;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorResult, setErrorResult] = useState<{
    reportId: string;
    horizon: number;
    message: string;
  } | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);
  const payload = result?.reportId === reportId && result.horizon === horizon ? result.data : null;
  const error = errorResult?.reportId === reportId && errorResult.horizon === horizon
    ? errorResult.message
    : null;
  const pending = loading || (!payload && !error);
  const frozenFeePercent = payload?.items.find(
    (item) => item.fee_policy?.fee_source === "user_assumption",
  )?.fee_policy?.round_trip_fee_percent;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErrorResult(null);
    void fetchDiscoveryOutcomes(reportId, horizon)
      .then((data) => {
        if (!cancelled) setResult({ reportId, horizon, data });
      })
      .catch((fetchError) => {
        if (!cancelled) {
          setErrorResult({
            reportId,
            horizon,
            message: fetchError instanceof Error ? fetchError.message : "荐基 T+N 复盘加载失败",
          });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [horizon, reportId, retrySequence]);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" aria-busy={pending}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900">
          <BarChart3 size={16} className="text-emerald-700" />
          荐基 T+N 复盘
        </h3>
        <div className="inline-flex rounded-xl bg-slate-100 p-1" aria-label="选择复盘周期">
          {HORIZONS.map((days) => (
            <button
              key={days}
              type="button"
              onClick={() => setHorizon(days)}
              aria-pressed={horizon === days}
              className={`rounded-lg px-3 py-1.5 text-xs font-black transition ${
                horizon === days ? "bg-slate-950 text-white shadow-sm" : "text-slate-600 hover:text-slate-950"
              }`}
            >
              T+{days}
            </button>
          ))}
        </div>
      </div>

      {pending ? (
        <div className="mt-3 flex items-center gap-2 text-xs text-slate-500" role="status">
          <Loader2 size={14} className="animate-spin" />
          正在核对 T+{horizon} 基金估值日…
        </div>
      ) : null}
      {error ? (
        <InlineNotice
          tone="error"
          message={`荐基 T+${horizon} 复盘加载失败：${error}`}
          action={{ label: "重试", onClick: () => setRetrySequence((current) => current + 1) }}
          className="mt-3"
        />
      ) : null}

      {payload ? (
        <div className="mt-4 space-y-3">
          <InlineNotice
            tone="info"
            message="只评价明确买入动作，并按日增长率优先的总收益率复盘；关注、观察、等待回调均单列。费用、正式基金合同基准或候选基线缺失时只降低覆盖率，不会被算成失败。"
          />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ["可评价", payload.eligible_count ?? 0],
              ["已成熟", payload.mature_count ?? 0],
              ["待成熟", payload.pending_count ?? 0],
              ["成熟覆盖", percent(payload.coverage_percent)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl bg-slate-50 px-3 py-3">
                <div className="text-[11px] text-slate-500">{label}</div>
                <div className="mt-1 text-lg font-black text-slate-950 tabular-nums">{value}</div>
              </div>
            ))}
          </div>
          <DecisionMetricGrid metrics={payload.metrics} />
          <FeeBenchmarkMethodNote feePercent={frozenFeePercent} />
          <LegacyReferenceStrip legacy={payload.legacy_reference} horizon={payload.horizon} />
          <p className="text-xs leading-5 text-slate-600">{payload.message}</p>
          {payload.items.length ? (
            <ul className="space-y-2">
              {payload.items.map((item, index) => {
                const meta = itemStatus(item);
                return (
                  <li key={`${item.fund_code}-${item.action}-${index}`} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-3 text-xs text-slate-700">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="font-semibold text-slate-900">[{item.fund_code}] {item.fund_name}</div>
                      <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
                    </div>
                    <div className="mt-1 leading-5">{item.assessment}</div>
                    <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] font-bold tabular-nums">
                      {item.positive_net_return_percent !== null && item.positive_net_return_percent !== undefined ? (
                        <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800">
                          假设费后 {signedPercent(item.positive_net_return_percent)}
                        </span>
                      ) : null}
                      {item.gross_excess_return_percent !== null && item.gross_excess_return_percent !== undefined ? (
                        <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-1 text-blue-800">
                          合同基准超额 {signedPercent(item.gross_excess_return_percent)}
                        </span>
                      ) : null}
                      {item.net_excess_return_percent !== null && item.net_excess_return_percent !== undefined ? (
                        <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-emerald-800">
                          费后合同超额 {signedPercent(item.net_excess_return_percent)}
                        </span>
                      ) : null}
                      {item.benchmark?.reference_return_percent !== null && item.benchmark?.reference_return_percent !== undefined ? (
                        <span className="rounded-full border border-slate-200 bg-white px-2 py-1 text-slate-600">
                          代理参考 {signedPercent(item.benchmark.reference_return_percent)} · 不计正式
                        </span>
                      ) : null}
                      {item.path_metrics?.available && item.path_metrics.max_adverse_excursion_percent != null ? (
                        <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-1 text-rose-800">
                          路径最不利 {signedPercent(item.path_metrics.max_adverse_excursion_percent)}
                        </span>
                      ) : null}
                      {item.no_action_counterfactual?.available && item.no_action_counterfactual.incremental_value_add_percent != null ? (
                        <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-1 text-violet-800">
                          相对不行动 {signedPercent(item.no_action_counterfactual.incremental_value_add_percent)}
                        </span>
                      ) : null}
                    </div>
                    {item.status === "pending" ? (
                      <div className="mt-1 text-slate-500">
                        已观察 {item.observed_forward_trading_days ?? 0}/{item.horizon_trading_days ?? horizon} 个估值日
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <InlineNotice tone="info" message="该报告暂无可复盘推荐条目。" />
          )}
        </div>
      ) : null}
    </section>
  );
}
