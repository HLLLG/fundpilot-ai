"use client";

import { useEffect, useMemo, useState } from "react";
import { LineChart } from "lucide-react";
import {
  fetchSectorSignalBacktest,
  type SectorSignalBacktest,
  type SectorSignalBacktestRule,
} from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

type SectorSignalBacktestPanelProps = {
  sectorLabels?: string[];
  lookbackDays?: number;
  title?: string;
  compact?: boolean;
};

function RuleCard({ rule }: { rule: SectorSignalBacktestRule }) {
  const hitRate = rule.hit_rate_percent;
  const tone = hitRate == null ? "blue" : hitRate >= 53 ? "green" : hitRate >= 50 ? "amber" : "red";

  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-black text-slate-950">{rule.label}</span>
        {hitRate != null ? (
          <StatusPill tone={tone}>命中率 {hitRate}%</StatusPill>
        ) : (
          <StatusPill tone="blue">无样本</StatusPill>
        )}
      </div>
      <p className="mt-2 text-xs text-slate-600">
        触发 {rule.trigger_count} 次 · 命中 {rule.hit_count}
        {rule.beats_random != null
          ? ` · ${rule.beats_random ? "高于" : "不高于"}随机 50%`
          : ""}
      </p>
    </div>
  );
}

export function SectorSignalBacktestPanel({
  sectorLabels,
  lookbackDays = 120,
  title = "板块信号回测",
  compact = false,
}: SectorSignalBacktestPanelProps) {
  const [data, setData] = useState<SectorSignalBacktest | null>(null);
  const [loading, setLoading] = useState(false);

  const sectorsKey = useMemo(
    () => (sectorLabels?.length ? [...sectorLabels].sort().join("|") : ""),
    [sectorLabels],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchSectorSignalBacktest(lookbackDays, sectorLabels)
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
  }, [lookbackDays, sectorsKey, sectorLabels]);

  if (loading && !data) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">{title}</h3>
        <p className="mt-2 text-sm text-slate-600">正在拉取板块日线并计算 T→T+1 命中率…</p>
      </section>
    );
  }

  if (!data) {
    return null;
  }

  if (data.enabled === false) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">{title}</h3>
        <p className="mt-2 text-sm text-slate-600">{data.message}</p>
      </section>
    );
  }

  if (!data.has_data) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">{title}</h3>
        <p className="mt-2 text-sm text-slate-600">
          {data.message ?? "暂无有效回测数据，请确认板块映射或稍后重试。"}
        </p>
      </section>
    );
  }

  const rules = Object.values(data.by_rule ?? {});

  return (
    <section className="glass-panel rounded-[24px] p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-indigo-500 text-white">
          <LineChart size={20} />
        </div>
        <div>
          <h3 className="text-lg font-black text-slate-950">{title}</h3>
          <p className="mt-1 text-xs text-slate-600">
            近 {data.lookback_days ?? lookbackDays} 交易日 · canonical 板块 {data.sector_count ?? 0} 个
            {sectorLabels?.length ? `（已按持仓板块筛选）` : "（全部硬编码板块）"}
          </p>
        </div>
      </div>

      <div className="space-y-2">
        {data.summary_lines?.map((line) => (
          <p key={line} className="text-sm leading-6 text-slate-700">
            {line}
          </p>
        ))}
      </div>

      {rules.length ? (
        <div className={`mt-4 grid gap-3 ${compact ? "sm:grid-cols-2" : "sm:grid-cols-2 lg:grid-cols-4"}`}>
          {rules.map((rule) => (
            <RuleCard key={rule.rule_id} rule={rule} />
          ))}
        </div>
      ) : null}

      {!compact && data.sectors?.length ? (
        <div className="mt-5 space-y-3">
          <div className="text-sm font-black text-slate-950">分板块明细</div>
          {data.sectors.map((sector) => (
            <div
              key={sector.sector_label}
              className="rounded-2xl border border-slate-100 bg-white/80 p-4"
            >
              <div className="mb-2 text-sm font-black text-slate-900">{sector.sector_label}</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {Object.values(sector.by_rule ?? {}).map((rule) => (
                  <RuleCard key={`${sector.sector_label}-${rule.rule_id}`} rule={rule} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
