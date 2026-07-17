"use client";

import { useEffect, useMemo, useState } from "react";
import { History, TimerReset } from "lucide-react";
import { fetchReportOutcomes, type ReportOutcomeItem, type ReportOutcomes } from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";
import { StatusPill } from "@/components/StatusPill";
import {
  DecisionMetricGrid,
  FeeBenchmarkMethodNote,
} from "@/components/DecisionMetricGrid";

type ReportOutcomesPanelProps = {
  reportId: string;
  embedded?: boolean;
};

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function signedPercent(value: number) {
  return `${value > 0 ? "+" : ""}${value}%`;
}

function statusMeta(item: ReportOutcomeItem, horizon: string) {
  const result = item.by_horizon?.[horizon];
  if (item.evaluation_class === "observation" || result?.status === "observation") {
    return { label: "观察单列", tone: "blue" as const };
  }
  if (result?.status === "mature") {
    return result.direction_hit
      ? { label: "方向一致", tone: "green" as const }
      : { label: "方向不一致", tone: "red" as const };
  }
  if (result?.status === "immature") return { label: "待成熟", tone: "amber" as const };
  if (result?.status === "data_unavailable") return { label: "数据缺口", tone: "amber" as const };
  return { label: "未评价", tone: "dark" as const };
}

function OutcomeItems({ outcomes, horizon }: { outcomes: ReportOutcomes; horizon: string }) {
  if (!outcomes.items.length) {
    return <InlineNotice tone="info" message="暂无基金级复盘明细；请等待样本成熟或检查净值数据源。" />;
  }
  return (
    <div className="space-y-2">
      {outcomes.items.map((item) => {
        const result = item.by_horizon?.[horizon];
        const meta = statusMeta(item, horizon);
        return (
          <div key={`${item.fund_code}-${item.action ?? item.current_action ?? "action"}`} className="rounded-2xl border border-slate-200/80 bg-white px-4 py-3 text-sm text-slate-700 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="font-black text-slate-950">{item.fund_name}（{item.fund_code}）</div>
                <div className="mt-1 text-xs text-slate-500">报告动作：{item.action ?? item.current_action ?? "—"}</div>
              </div>
              <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
            </div>
            <div className="mt-2 text-xs leading-5 text-slate-600">
              {result?.return_percent !== undefined
                ? `${horizon} 总收益 ${result.return_percent > 0 ? "+" : ""}${result.return_percent}% · 目标净值日 ${result.target_nav_date ?? "—"}`
                : result?.status === "immature"
                  ? `${horizon} 尚未成熟，当前只有 ${result.available_forward_trading_days ?? 0} 个后续净值日。`
                  : item.assessment}
            </div>
            {result ? (
              <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] font-bold tabular-nums">
                {result.positive_net_return_percent !== null && result.positive_net_return_percent !== undefined ? (
                  <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800">
                    假设费后 {signedPercent(result.positive_net_return_percent)}
                  </span>
                ) : null}
                {result.gross_excess_return_percent !== null && result.gross_excess_return_percent !== undefined ? (
                  <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-1 text-blue-800">
                    合同基准超额 {signedPercent(result.gross_excess_return_percent)}
                  </span>
                ) : null}
                {result.net_excess_return_percent !== null && result.net_excess_return_percent !== undefined ? (
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-emerald-800">
                    费后合同超额 {signedPercent(result.net_excess_return_percent)}
                  </span>
                ) : null}
                {result.benchmark?.reference_return_percent !== null && result.benchmark?.reference_return_percent !== undefined ? (
                  <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
                    代理参考 {signedPercent(result.benchmark.reference_return_percent)} · 不计正式
                  </span>
                ) : null}
                {result.path_metrics?.available && result.path_metrics.max_adverse_excursion_percent != null ? (
                  <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-1 text-rose-800">
                    路径最不利 {signedPercent(result.path_metrics.max_adverse_excursion_percent)}
                  </span>
                ) : null}
                {result.path_metrics?.available && result.path_metrics.max_favorable_excursion_percent != null ? (
                  <span className="rounded-full border border-cyan-200 bg-cyan-50 px-2 py-1 text-cyan-800">
                    路径最有利 {signedPercent(result.path_metrics.max_favorable_excursion_percent)}
                  </span>
                ) : null}
                {result.no_action_counterfactual?.available && result.no_action_counterfactual.incremental_value_add_percent != null ? (
                  <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-1 text-violet-800">
                    相对不行动 {signedPercent(result.no_action_counterfactual.incremental_value_add_percent)}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function ReportOutcomesPanel({ reportId, embedded = false }: ReportOutcomesPanelProps) {
  const [result, setResult] = useState<{ reportId: string; data: ReportOutcomes } | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorResult, setErrorResult] = useState<{ reportId: string; message: string } | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);
  const [activeHorizon, setActiveHorizon] = useState("T+5");
  const outcomes = result?.reportId === reportId ? result.data : null;
  const error = errorResult?.reportId === reportId ? errorResult.message : null;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErrorResult(null);
    void fetchReportOutcomes(reportId)
      .then((data) => {
        if (!cancelled) {
          setResult({ reportId, data });
          const first = Object.keys(data.by_horizon ?? {})[0];
          if (first) setActiveHorizon(first);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setErrorResult({
            reportId,
            message: loadError instanceof Error ? loadError.message : "T+N 复盘加载失败",
          });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reportId, retrySequence]);

  const horizonEntries = useMemo(() => Object.entries(outcomes?.by_horizon ?? {}), [outcomes]);
  const activeStats = outcomes?.by_horizon?.[activeHorizon];
  const frozenFeePercent = outcomes?.items.find(
    (item) => item.fee_policy?.fee_source === "user_assumption",
  )?.fee_policy?.round_trip_fee_percent;
  const pending = loading || (!outcomes && !error);

  const body = (
    <div className="space-y-4" aria-busy={pending}>
      {pending ? <InlineNotice tone="info" message={outcomes ? "正在更新成熟样本…" : "正在计算 T+N 净值复盘…"} /> : null}
      {error ? (
        <InlineNotice
          tone={outcomes ? "warning" : "error"}
          message={outcomes ? `T+N 复盘更新失败，继续显示上次结果：${error}` : `T+N 复盘加载失败：${error}`}
          action={{ label: "重试", onClick: () => setRetrySequence((current) => current + 1) }}
        />
      ) : null}

      {outcomes ? (
        <>
          <InlineNotice
            tone="info"
            message="结果按基金自身估值日和总收益率计算；观察/复核动作单列。缺少费用假设、完整基金合同基准或冻结仓位变化时，对应指标显示未覆盖，不会被算成命中或失败。"
          />
          {outcomes.message ? <p className="text-sm leading-6 text-slate-700">{outcomes.message}</p> : null}
          {horizonEntries.length ? (
            <div className="grid gap-2 sm:grid-cols-3">
              {horizonEntries.map(([label, stats]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => setActiveHorizon(label)}
                  aria-pressed={activeHorizon === label}
                  className={`rounded-2xl border px-4 py-3 text-left transition ${
                    activeHorizon === label
                      ? "border-slate-950 bg-slate-950 text-white shadow-md"
                      : "border-slate-200 bg-white text-slate-700 hover:border-slate-400"
                  }`}
                >
                  <div className="text-xs font-black tracking-[0.12em]">{label}</div>
                  <div className="mt-2 text-sm font-bold tabular-nums">成熟 {stats.mature_count}/{stats.eligible_count}</div>
                  <div className={`mt-1 text-[11px] ${activeHorizon === label ? "text-slate-300" : "text-slate-500"}`}>
                    覆盖 {percent(stats.coverage_percent)} · 方向吻合 {percent(stats.hit_rate_percent)}
                  </div>
                </button>
              ))}
            </div>
          ) : null}
          {activeStats ? (
            <>
              <DecisionMetricGrid metrics={activeStats.metrics} />
              <FeeBenchmarkMethodNote feePercent={frozenFeePercent} />
              <div className="flex flex-wrap gap-2 text-xs text-slate-600">
                <StatusPill tone="green">成熟 {activeStats.mature_count}</StatusPill>
                <StatusPill tone="amber">未成熟/跳过 {activeStats.skipped_count}</StatusPill>
                <span className="self-center">观察/复核 {outcomes.observation_count ?? 0} 条单列</span>
              </div>
            </>
          ) : null}
          <OutcomeItems outcomes={outcomes} horizon={activeHorizon} />
        </>
      ) : null}
    </div>
  );

  if (embedded) return body;
  return (
    <div className="mb-5 rounded-[24px] border border-slate-200 bg-slate-50/80 p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
        <History size={18} className="text-emerald-700" />
        建议 T+N 复盘
        <TimerReset size={14} className="text-slate-400" />
      </div>
      {body}
    </div>
  );
}
