"use client";

import { useEffect, useState } from "react";
import { BarChart3, Loader2 } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import type { DiscoveryOutcomesPayload } from "@/lib/api";
import { fetchDiscoveryOutcomes } from "@/lib/api";

type DiscoveryOutcomesPanelProps = {
  reportId: string;
};

export function DiscoveryOutcomesPanel({ reportId }: DiscoveryOutcomesPanelProps) {
  const [result, setResult] = useState<{
    reportId: string;
    data: DiscoveryOutcomesPayload;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorResult, setErrorResult] = useState<{
    reportId: string;
    message: string;
  } | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);
  const payload = result?.reportId === reportId ? result.data : null;
  const error = errorResult?.reportId === reportId ? errorResult.message : null;
  const pending = loading || (!payload && !error);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErrorResult(null);
    void fetchDiscoveryOutcomes(reportId, 7)
      .then((data) => {
        if (!cancelled) setResult({ reportId, data });
      })
      .catch((fetchError) => {
        if (!cancelled) {
          setErrorResult({
            reportId,
            message: fetchError instanceof Error ? fetchError.message : "加载复盘失败",
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

  return (
    <section
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
      aria-busy={pending}
    >
      <h3 className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-900">
        <BarChart3 size={16} className="text-emerald-700" />
        推荐复盘（约 7 日）
      </h3>
      {pending ? (
        <div className="flex items-center gap-2 text-xs text-slate-500" role="status">
          <Loader2 size={14} className="animate-spin" />
          {payload ? "正在更新复盘，当前继续显示已有结果…" : "计算净值变化…"}
        </div>
      ) : null}
      {error ? (
        <InlineNotice
          tone={payload ? "warning" : "error"}
          message={
            payload
              ? `推荐复盘更新失败，继续显示上次成功获取的结果：${error}`
              : `推荐复盘加载失败：${error}`
          }
          action={{
            label: "重试",
            onClick: () => setRetrySequence((current) => current + 1),
          }}
        />
      ) : null}
      {payload ? (
        <>
          {payload.has_data && payload.items.length ? (
            <p className="mt-3 text-xs leading-5 text-slate-600">{payload.message}</p>
          ) : (
            <InlineNotice
              tone="info"
              message={payload.message || "复盘已计算，但暂无可展示的结果。"}
              className="mt-3"
            />
          )}
          {payload.has_data && payload.items.length ? (
            <ul className="mt-3 space-y-2">
              {payload.items.map((item) => (
                <li
                  key={item.fund_code}
                  className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-700"
                >
                  <div className="font-semibold text-slate-900">
                    [{item.fund_code}] {item.fund_name}
                  </div>
                  <div className="mt-1">{item.assessment}</div>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
