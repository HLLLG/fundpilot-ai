"use client";

import { BarChart3, Loader2 } from "lucide-react";
import {
  fetchFundReturnDistribution,
  type FundReturnDistribution,
  type FundReturnDistributionBinKey,
} from "@/lib/api";
import { useCachedFetch } from "@/lib/useCachedFetch";

const CACHE_KEY = "diagnostics:fund-return-distribution";
const STALE_MS = 30 * 60_000;

const BINS: Array<{
  key: FundReturnDistributionBinKey;
  label: string;
  tone: "down" | "flat" | "up";
}> = [
  { key: "le_neg5", label: "≤-5", tone: "down" },
  { key: "neg5_neg3", label: "-5~-3", tone: "down" },
  { key: "neg3_neg1", label: "-3~-1", tone: "down" },
  { key: "neg1_zero", label: "-1~0", tone: "down" },
  { key: "zero", label: "0", tone: "flat" },
  { key: "zero_one", label: "0~1", tone: "up" },
  { key: "one_three", label: "1~3", tone: "up" },
  { key: "three_five", label: "3~5", tone: "up" },
  { key: "ge_five", label: "≥5", tone: "up" },
];

const BAR_TONE = {
  down: "bg-emerald-500",
  flat: "bg-slate-300",
  up: "bg-rose-500",
} as const;

function formatCount(value: number | null | undefined): string {
  return new Intl.NumberFormat("zh-CN").format(value ?? 0);
}

function ratio(value: number | null | undefined, total: number): number {
  if (value == null || total <= 0) {
    return 0;
  }
  return Math.max(0, (value / total) * 100);
}

function DistributionContent({ data }: { data: FundReturnDistribution }) {
  const values = BINS.map((bin) => ({ ...bin, count: data.bins?.[bin.key] ?? 0 }));
  const maxCount = Math.max(1, ...values.map((item) => item.count));
  const total = data.valid_count ?? values.reduce((sum, item) => sum + item.count, 0);
  const decline = data.decline_count ?? 0;
  const advance = data.advance_count ?? 0;
  const flat = data.flat_count ?? 0;

  return (
    <>
      <div className="-mx-1 mt-5 overflow-x-auto px-1 pb-1">
        <div className="min-w-[610px]">
          <div
            className="grid h-44 grid-cols-9 items-end gap-2 border-b border-slate-200 px-1"
            aria-label="基金日增长率九档分布"
          >
            {values.map((item) => {
              const height = item.count > 0 ? Math.max(4, (item.count / maxCount) * 100) : 0;
              return (
                <div key={item.key} className="flex h-full min-w-0 flex-col justify-end text-center">
                  <span className="mb-1 text-[11px] font-bold tabular-nums text-slate-700">
                    {formatCount(item.count)}
                  </span>
                  <div className="flex h-[126px] items-end justify-center">
                    <div
                      className={`w-full max-w-10 rounded-t-sm ${BAR_TONE[item.tone]} transition-[height] duration-500`}
                      style={{ height: `${height}%` }}
                      title={`${item.label}%：${formatCount(item.count)}只`}
                    />
                  </div>
                  <span className="mt-2 whitespace-nowrap text-[10px] font-semibold tabular-nums text-slate-500">
                    {item.label}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-2 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
        <div className="flex items-baseline gap-2 text-emerald-700">
          <span className="text-xs font-bold">下跌</span>
          <strong className="font-serif text-2xl tabular-nums">{formatCount(decline)}</strong>
          <span className="text-xs font-semibold">{ratio(decline, total).toFixed(1)}%</span>
        </div>
        <div
          className="flex h-3 min-w-48 overflow-hidden rounded-full bg-slate-100 ring-1 ring-slate-200"
          aria-label={`下跌${formatCount(decline)}只，平盘${formatCount(flat)}只，上涨${formatCount(advance)}只`}
        >
          <span className="bg-emerald-500" style={{ width: `${ratio(decline, total)}%` }} />
          <span className="bg-slate-300" style={{ width: `${ratio(flat, total)}%` }} />
          <span className="bg-rose-500" style={{ width: `${ratio(advance, total)}%` }} />
        </div>
        <div className="flex items-baseline justify-start gap-2 text-rose-700 sm:justify-end">
          <span className="text-xs font-bold">上涨</span>
          <strong className="font-serif text-2xl tabular-nums">{formatCount(advance)}</strong>
          <span className="text-xs font-semibold">{ratio(advance, total).toFixed(1)}%</span>
        </div>
      </div>

      <p className="mt-4 text-xs leading-5 text-slate-500">
        统计 {formatCount(total)} 个有效基金份额代码；A/C/E 等份额分别计数。
        {data.missing_count ? `另有 ${formatCount(data.missing_count)} 只缺少当日增长率，未纳入柱状图。` : ""}
        {data.coverage_percent != null ? ` 数据覆盖率 ${data.coverage_percent.toFixed(1)}%。` : ""}
      </p>
    </>
  );
}

export function FundReturnDistributionPanel() {
  const { data, error, loading, revalidating } = useCachedFetch<FundReturnDistribution>({
    cacheKey: CACHE_KEY,
    fetcher: fetchFundReturnDistribution,
    staleTimeMs: STALE_MS,
  });

  return (
    <section className="mt-4 min-w-0 max-w-full overflow-hidden rounded-2xl border border-slate-200/90 bg-[#fbfaf7] px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-[#f4ead2]">
            <BarChart3 size={18} aria-hidden />
          </div>
          <div>
            <h4 className="text-base font-black text-slate-950">基金涨跌分布</h4>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              官方净值 · {data?.as_of_date ? `截至 ${data.as_of_date}` : "正在确认净值日期"}
              {data?.stale ? " · 上次成功统计" : ""}
            </p>
          </div>
        </div>
        {revalidating ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-slate-500" role="status">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />更新中
          </span>
        ) : null}
      </div>

      {loading && !data ? (
        <div className="mt-5 flex h-44 items-center justify-center rounded-xl bg-white/60 text-sm text-slate-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          正在聚合全量官方净值…
        </div>
      ) : data?.available ? (
        <DistributionContent data={data} />
      ) : (
        <p className="mt-4 rounded-xl bg-white/70 px-3 py-3 text-sm leading-6 text-slate-600" role="status">
          {data?.message ?? "基金官方净值分布暂不可用。"}
        </p>
      )}

      {error ? (
        <p className="mt-3 text-xs font-semibold text-amber-700" role="status">
          本次更新失败；如有历史结果仍会保留展示。
        </p>
      ) : null}
    </section>
  );
}
