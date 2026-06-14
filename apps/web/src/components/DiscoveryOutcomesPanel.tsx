"use client";

import { useEffect, useState } from "react";
import { BarChart3, Loader2 } from "lucide-react";
import type { DiscoveryOutcomesPayload } from "@/lib/api";
import { fetchDiscoveryOutcomes } from "@/lib/api";

type DiscoveryOutcomesPanelProps = {
  reportId: string;
};

export function DiscoveryOutcomesPanel({ reportId }: DiscoveryOutcomesPanelProps) {
  const [payload, setPayload] = useState<DiscoveryOutcomesPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchDiscoveryOutcomes(reportId, 7)
      .then((data) => {
        if (!cancelled) setPayload(data);
      })
      .catch((fetchError) => {
        if (!cancelled) {
          setError(fetchError instanceof Error ? fetchError.message : "加载复盘失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reportId]);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-900">
        <BarChart3 size={16} className="text-emerald-600" />
        推荐复盘（约 7 日）
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <Loader2 size={14} className="animate-spin" />
          计算净值变化…
        </div>
      ) : null}
      {error ? <p className="text-xs text-red-600">{error}</p> : null}
      {!loading && payload ? (
        <>
          <p className="text-xs leading-5 text-slate-600">{payload.message}</p>
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
