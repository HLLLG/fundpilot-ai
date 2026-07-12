"use client";

import { useCallback, useEffect, useState } from "react";
import { Newspaper, RefreshCw } from "lucide-react";
import type { Holding, InvestorProfile, NewsPreviewResponse } from "@/lib/api";
import { previewNewsForHoldings } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

const freshnessTone = {
  fresh: "green",
  moderate: "amber",
  aging: "amber",
  stale: "red",
  empty: "blue",
  today_unknown_time: "amber",
} as const;

type NewsPreviewPanelProps = {
  holdings: Holding[];
  profile: InvestorProfile;
};

export function NewsPreviewPanel({ holdings, profile }: NewsPreviewPanelProps) {
  const [preview, setPreview] = useState<NewsPreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!holdings.length) {
      setPreview(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await previewNewsForHoldings(holdings, profile);
      setPreview(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "新闻预取失败");
      setPreview(null);
    } finally {
      setLoading(false);
    }
  }, [holdings, profile]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!holdings.length) {
    return null;
  }

  const freshness = preview?.freshness;
  const tone: "green" | "amber" | "red" | "blue" | "dark" =
    freshness?.freshness_label &&
    Object.prototype.hasOwnProperty.call(freshnessTone, freshness.freshness_label)
      ? freshnessTone[freshness.freshness_label as keyof typeof freshnessTone]
      : "blue";

  return (
    <section className="glass-panel rounded-[24px] p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="mb-2 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-sky-700 text-white">
            <Newspaper size={20} />
          </div>
          <h3 className="text-lg font-black text-slate-950">要闻时效自检</h3>
          <p className="mt-1 text-xs leading-5 text-slate-600">
            生成日报前预览将喂给 DeepSeek 的东财要闻（不消耗模型额度）。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex min-h-11 items-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:border-blue-300 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          刷新
        </button>
      </div>

      {error ? (
        <p role="alert" className="rounded-2xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </p>
      ) : null}

      {freshness ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={tone}>
              时效 {freshness.freshness_label}
            </StatusPill>
            <span className="text-xs font-semibold text-slate-600">
              当日 {freshness.today_items}/{freshness.total_items} 条
              {freshness.median_age_minutes != null
                ? ` · 中位龄 ${freshness.median_age_minutes} 分钟`
                : ""}
            </span>
          </div>
          <p className="text-sm leading-6 text-slate-700">{freshness.interpretation}</p>
          {preview?.topics.length ? (
            <p className="text-xs text-slate-500">
              检索主题：{preview.topics.join("、")}
            </p>
          ) : null}
          <ul className="max-h-40 space-y-1 overflow-y-auto text-xs text-slate-600">
            {(preview?.items ?? []).slice(0, 6).map((item, index) => (
              <li key={`${item.topic}-${index}`} className="rounded-lg bg-white/80 px-2 py-1">
                {item.is_today ? "【今】" : "【旧】"}
                {item.published_at ? ` ${item.published_at.slice(0, 16)}` : ""} {item.title}
              </li>
            ))}
          </ul>
        </div>
      ) : loading ? (
        <p className="text-sm text-slate-500">正在拉取要闻…</p>
      ) : null}
    </section>
  );
}
