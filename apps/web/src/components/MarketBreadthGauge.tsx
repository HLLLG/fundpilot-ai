"use client";

import { useEffect, useState } from "react";
import { Gauge, Loader2 } from "lucide-react";
import { fetchMarketBreadth, type MarketBreadthSignal } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

type MarketBreadthGaugeProps = {
  compact?: boolean;
};

const SENTIMENT_TONE: Record<string, "blue" | "green" | "amber" | "red" | "dark"> = {
  冰点: "red",
  低迷: "amber",
  中性: "blue",
  偏热: "green",
  亢奋: "dark",
};

function formatYi(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)} 亿`;
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(1)}%`;
}

function formatCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return String(value);
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-white px-3 py-2 ring-1 ring-slate-100">
      <div className="text-[10px] font-bold text-slate-500">{label}</div>
      <div className="mt-0.5 break-words text-sm font-semibold text-slate-800">{value}</div>
    </div>
  );
}

/**
 * M1.1/M5：大盘情绪温度计。挂载在市场 Tab（全市场自上而下参考）与生成日报
 * 诊断区 `DiagnosticsAccordion`（辅助判断当日决策是否要更谨慎/更果断）。
 * 自行请求数据（对齐 SectorSignalBacktestPanel 的自包含模式），两处挂载点
 * 无需各自维护数据获取逻辑。
 */
export function MarketBreadthGauge({ compact = false }: MarketBreadthGaugeProps) {
  const [data, setData] = useState<MarketBreadthSignal | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchMarketBreadth()
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setData(null);
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
  }, []);

  if (loading && !data) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">大盘情绪温度计</h3>
        <div className="mt-2 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          正在计算全市场创新高低家数分布…
        </div>
      </section>
    );
  }

  if (!data || !data.available) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">大盘情绪温度计</h3>
        <p className="mt-2 text-sm text-slate-600">
          {data?.message ?? "情绪温度计暂不可用，不影响其余分析结果。"}
        </p>
      </section>
    );
  }

  const tone = SENTIMENT_TONE[data.sentiment_level ?? ""] ?? "blue";
  const changeText =
    data.sentiment_level_change != null && data.sentiment_level_change !== 0
      ? data.sentiment_level_change < 0
        ? `较上一交易日转冷 ${Math.abs(data.sentiment_level_change)} 档`
        : `较上一交易日转热 ${data.sentiment_level_change} 档`
      : null;

  return (
    <section className="glass-panel rounded-[24px] p-5" data-testid="market-breadth-gauge">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[var(--brand)] text-white">
            <Gauge size={20} />
          </div>
          <div>
            <h3 className="text-lg font-black text-slate-950">大盘情绪温度计</h3>
            <p className="mt-1 text-xs text-slate-600">
              {data.trade_date ?? "—"}
              {data.stale ? "（上次缓存）" : ""}
              {data.breadth_sample_days ? ` · 近 ${data.breadth_sample_days} 个交易日分布` : ""}
            </p>
          </div>
        </div>
        <StatusPill tone={tone}>{data.sentiment_level ?? "—"}</StatusPill>
      </div>

      {data.interpretation ? (
        <p className="mt-3 text-sm leading-6 text-slate-700">
          {data.interpretation}
          {changeText ? `（${changeText}）` : ""}
        </p>
      ) : null}

      <div className={`mt-4 grid gap-2 ${compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-4"}`}>
        <Metric label="涨停家数" value={formatCount(data.limit_up_count)} />
        <Metric label="跌停家数" value={formatCount(data.limit_down_count)} />
        <Metric label="炸板率" value={formatPercent(data.limit_up_broken_ratio_percent)} />
        <Metric label="最高连板" value={formatCount(data.max_consecutive_boards)} />
      </div>
      {data.margin_available ? (
        <div className="mt-2 grid grid-cols-1">
          <Metric
            label={`两融余额环比${data.margin_scope === "sse_only" ? "（仅沪市）" : ""}`}
            value={formatYi(data.margin_balance_change_yi)}
          />
        </div>
      ) : null}

      <p className="mt-3 text-xs leading-5 text-slate-500">
        情绪档位基于近2年全市场创新高/创新低家数分布自校准；涨跌停/炸板/连板为当日快照，非历史回测结论，仅供辅助参考，不构成投资建议。
      </p>
    </section>
  );
}
