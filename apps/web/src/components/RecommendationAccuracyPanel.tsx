"use client";

import { useEffect, useState } from "react";
import { Activity, FlaskConical } from "lucide-react";
import type { OutcomeHorizonStats, RecommendationAccuracy } from "@/lib/api";
import { fetchRecommendationAccuracy } from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";
import { StatusPill } from "@/components/StatusPill";
import {
  DecisionMetricGrid,
  FeeBenchmarkMethodNote,
  LegacyReferenceStrip,
} from "@/components/DecisionMetricGrid";

const STYLE_LABELS: Record<string, string> = {
  tactical: "战术短线",
  aggressive: "激进波段",
  conservative: "稳健",
};

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function HorizonCard({ label, stats }: { label: string; stats?: OutcomeHorizonStats }) {
  const mature = stats?.mature_count ?? 0;
  const eligible = stats?.eligible_count ?? 0;
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white/90 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-black tracking-[0.12em] text-slate-500">{label}</span>
        <StatusPill tone={mature ? "green" : "amber"}>{mature ? "已成熟" : "待成熟"}</StatusPill>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 tabular-nums">
        <div>
          <div className="text-[11px] text-slate-500">成熟样本</div>
          <div className="mt-1 text-xl font-black text-slate-950">
            {mature}<span className="text-sm text-slate-400">/{eligible}</span>
          </div>
        </div>
        <div>
          <div className="text-[11px] text-slate-500">方向吻合</div>
          <div className="mt-1 text-xl font-black text-slate-950">
            {percent(stats?.hit_rate_percent)}
          </div>
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
        <div
          className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
          style={{ width: `${Math.max(0, Math.min(stats?.coverage_percent ?? 0, 100))}%` }}
        />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">成熟覆盖率 {percent(stats?.coverage_percent)}</p>
    </div>
  );
}

export function RecommendationAccuracyPanel() {
  const [data, setData] = useState<RecommendationAccuracy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchRecommendationAccuracy(30)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "T+N 复盘加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [retrySequence]);

  const horizonEntries = Object.entries(data?.by_horizon ?? {});
  const styles = Object.values(data?.by_style ?? {});
  const primaryKey = horizonEntries[0]?.[0] ?? "T+1";
  const primaryMetrics = data?.metrics ?? data?.by_horizon?.[primaryKey]?.metrics;

  return (
    <section className="glass-panel rounded-[24px] p-5" aria-busy={loading}>
      <div className="mb-4 flex items-start gap-3">
        <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-slate-950 text-emerald-300 shadow-sm">
          <FlaskConical size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-black text-slate-950">T+N 决策复盘</h3>
            <StatusPill tone="amber">仅人工复盘</StatusPill>
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-600">
            按基金自身估值日成熟度评价；同日多份日报只保留最后一版。
            {data ? ` 当前正式 V2 报告 ${data.formal_v2_report_count ?? 0} 份，选取 ${data.selected_report_count ?? data.report_count ?? 0} 个报告日。` : ""}
          </p>
        </div>
      </div>

      <InlineNotice
        tone="warning"
        message={
          data?.warning ??
          "当前已拆分方向、用户假设费后和正式基金合同基准超额；真实交易费用与样本量达标前，不会用于自动调参。"
        }
        className="mb-4"
      />

      {error ? (
        <InlineNotice
          tone={data ? "warning" : "error"}
          message={data ? `T+N 复盘更新失败，继续显示上次结果：${error}` : `T+N 复盘加载失败：${error}`}
          action={{ label: "重试", onClick: () => setRetrySequence((current) => current + 1) }}
          className="mb-4"
        />
      ) : loading ? (
        <InlineNotice tone="info" message={data ? "正在更新成熟样本…" : "正在计算 T+N 成熟样本…"} className="mb-4" />
      ) : null}

      {horizonEntries.length ? (
        <div className="grid gap-3 sm:grid-cols-3">
          {horizonEntries.map(([label, stats]) => (
            <HorizonCard key={label} label={label} stats={stats} />
          ))}
        </div>
      ) : data && !data.has_enough_data ? (
        <InlineNotice tone="info" message={data.message ?? "暂无成熟的方向建议样本。"} />
      ) : null}

      {data ? (
        <div className="mt-4 space-y-3">
          <DecisionMetricGrid metrics={primaryMetrics} />
          <FeeBenchmarkMethodNote />
          <LegacyReferenceStrip legacy={data.legacy_reference} horizon={primaryKey} />
        </div>
      ) : null}

      {styles.length ? (
        <div className="mt-4 rounded-2xl border border-slate-200/80 bg-slate-50/80 p-4">
          <div className="mb-3 flex items-center gap-2 text-xs font-black tracking-[0.08em] text-slate-600">
            <Activity size={14} className="text-emerald-700" />
            按决策风格 · {primaryKey}
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            {styles.map((bucket) => {
              const stats = bucket.by_horizon?.[primaryKey];
              return (
                <div key={bucket.decision_style} className="rounded-xl bg-white px-3 py-3 shadow-sm">
                  <div className="text-sm font-black text-slate-950">
                    {STYLE_LABELS[bucket.decision_style] ?? bucket.decision_style}
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-600 tabular-nums">
                    成熟 {stats?.mature_count ?? bucket.mature_count ?? 0}/
                    {stats?.eligible_count ?? bucket.eligible_count ?? 0} · 方向吻合 {percent(stats?.hit_rate_percent)}
                  </p>
                  <p className="text-[11px] text-slate-500">
                    观察/复核 {bucket.observation_count ?? 0} 条，不进入分母
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      ) : data?.has_enough_data ? (
        <InlineNotice tone="info" message="复盘已生成，但暂无可展示的决策风格样本。" className="mt-4" />
      ) : null}
    </section>
  );
}
