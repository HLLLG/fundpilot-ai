"use client";

import { useEffect, useState } from "react";
import { Target } from "lucide-react";
import type { RecommendationAccuracy } from "@/lib/api";
import { fetchRecommendationAccuracy } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

export function RecommendationAccuracyPanel() {
  const [data, setData] = useState<RecommendationAccuracy | null>(null);

  useEffect(() => {
    void fetchRecommendationAccuracy(30)
      .then(setData)
      .catch(() => setData(null));
  }, []);

  if (!data) {
    return null;
  }

  if (!data.has_enough_data) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">建议准确率看板</h3>
        <p className="mt-2 text-sm text-slate-600">{data.message}</p>
      </section>
    );
  }

  const styles = Object.values(data.by_style ?? {});

  return (
    <section className="glass-panel rounded-[24px] p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-violet-500 text-white">
          <Target size={20} />
        </div>
        <div>
          <h3 className="text-lg font-black text-slate-950">建议准确率看板</h3>
          <p className="mt-1 text-xs text-slate-600">
            基于最近 {data.report_count} 份日报、{data.paired_days} 组相邻交易日复盘（按决策风格分桶）。
          </p>
        </div>
      </div>

      <div className="space-y-3">
        {data.summary_lines?.map((line) => (
          <p key={line} className="text-sm leading-6 text-slate-700">
            {line}
          </p>
        ))}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {styles.map((bucket) => (
          <div key={bucket.decision_style} className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-black text-slate-950">
                {bucket.decision_style === "tactical"
                  ? "战术短线"
                  : bucket.decision_style === "aggressive"
                    ? "激进波段"
                    : "稳健"}
              </span>
              <StatusPill tone={bucket.hit_rate_percent >= 50 ? "green" : "amber"}>
                命中率 {bucket.hit_rate_percent}%
              </StatusPill>
            </div>
            <p className="mt-2 text-xs text-slate-600">
              样本 {bucket.paired_count} 组 · 命中 {bucket.hit_count} · 需复盘 {bucket.miss_count}
            </p>
            {bucket.reversal?.up_then_down_count ? (
              <p className="mt-2 text-xs leading-5 text-amber-900">
                涨后回吐 {bucket.reversal.up_then_down_count} 次，追涨加仓{" "}
                {bucket.reversal.up_then_down_aggressive_miss} 次
                {bucket.reversal.aggressive_miss_rate_percent != null
                  ? `（${bucket.reversal.aggressive_miss_rate_percent}%）`
                  : ""}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
