"use client";

import { useEffect, useState } from "react";
import { Target } from "lucide-react";
import type { RecommendationAccuracy } from "@/lib/api";
import { fetchRecommendationAccuracy } from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";
import { StatusPill } from "@/components/StatusPill";

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
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "建议准确率加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [retrySequence]);

  const styles = Object.values(data?.by_style ?? {});

  return (
    <section className="glass-panel rounded-[24px] p-5" aria-busy={loading}>
      <div className="mb-4 flex items-start gap-3">
        <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[var(--brand)] text-white">
          <Target size={20} />
        </div>
        <div>
          <h3 className="text-lg font-black text-slate-950">建议准确率看板</h3>
          {data?.has_enough_data ? (
            <p className="mt-1 text-xs text-slate-600">
              基于最近 {data.report_count ?? 0} 份日报、{data.paired_days ?? 0}
              组相邻交易日复盘（按决策风格分桶）。
            </p>
          ) : null}
        </div>
      </div>

      {error ? (
        <InlineNotice
          tone={data ? "warning" : "error"}
          message={
            data
              ? `建议准确率更新失败，继续显示上次成功获取的统计：${error}`
              : `建议准确率加载失败：${error}`
          }
          action={{
            label: "重试",
            onClick: () => setRetrySequence((current) => current + 1),
          }}
          className="mb-4"
        />
      ) : loading ? (
        <InlineNotice
          tone="info"
          message={data ? "正在更新建议准确率，当前继续显示已有统计。" : "正在加载建议准确率…"}
          className="mb-4"
        />
      ) : null}

      {data && !data.has_enough_data ? (
        <InlineNotice
          tone="info"
          message={data.message ?? "可配对的历史日报不足，暂无法计算建议准确率。"}
        />
      ) : null}

      {data?.has_enough_data ? <div className="space-y-3">
        {data.summary_lines?.map((line) => (
          <p key={line} className="text-sm leading-6 text-slate-700">
            {line}
          </p>
        ))}
      </div> : null}

      {data?.has_enough_data && styles.length === 0 ? (
        <InlineNotice
          tone="info"
          message="准确率统计已生成，但暂无可展示的决策风格样本。"
          className="mt-4"
        />
      ) : null}

      {data?.has_enough_data && styles.length > 0 ? <div className="mt-4 grid gap-3 sm:grid-cols-2">
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
      </div> : null}
    </section>
  );
}
