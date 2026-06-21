"use client";

import { useMemo, useState } from "react";
import { Loader2, RotateCcw, Sparkles } from "lucide-react";
import type { DipRadarResponse } from "@/lib/dipRadar";
import {
  DIP_RADAR_DISCLAIMER,
  formatDipPercent,
  formatDipRadarUpdatedFromIso,
  formatReboundScore,
  formatReboundSignals,
  isDipRadarUsable,
  openDipSwingDiscovery,
  profitToneClass,
  reboundScoreTone,
} from "@/lib/dipRadar";

type DipReboundRadarProps = {
  data: DipRadarResponse | null;
  loading: boolean;
  revalidating?: boolean;
  lookbackDays: 3 | 5;
  onLookbackDaysChange: (days: 3 | 5) => void;
  sectorFilter: string | null;
  onSectorFilterChange: (sector: string | null) => void;
  onRefresh: () => void;
  onOpenFund?: (fundCode: string, fundName: string) => void;
};

export function DipReboundRadar({
  data,
  loading,
  revalidating = false,
  lookbackDays,
  onLookbackDaysChange,
  sectorFilter,
  onSectorFilterChange,
  onRefresh,
  onOpenFund,
}: DipReboundRadarProps) {
  const [pendingSector, setPendingSector] = useState<string | null>(null);

  const leaders = useMemo(() => data?.sector_dip_leaders ?? [], [data?.sector_dip_leaders]);
  const items = data?.items ?? [];
  const showEmpty = !loading && !isDipRadarUsable(data);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-bold text-slate-800">大跌反弹雷达</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            近 {lookbackDays} 日净值跌幅较深、附带反弹信号的场外基金
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="tab-segment text-xs">
            <button
              type="button"
              className="tab-segment-btn px-3 py-1.5"
              aria-pressed={lookbackDays === 3}
              onClick={() => onLookbackDaysChange(3)}
            >
              3 日
            </button>
            <button
              type="button"
              className="tab-segment-btn px-3 py-1.5"
              aria-pressed={lookbackDays === 5}
              onClick={() => onLookbackDaysChange(5)}
            >
              5 日
            </button>
          </div>
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-1.5 px-3 py-1.5 text-xs"
            onClick={onRefresh}
            disabled={revalidating}
          >
            {revalidating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            刷新
          </button>
        </div>
      </div>

      {sectorFilter ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-100 bg-amber-50/80 px-3 py-2 text-xs text-amber-900">
          <span>
            当前仅显示「<strong>{sectorFilter}</strong>」板块
            {data?.scan_stats?.total_matches != null && (data.scan_stats.matches ?? 0) === 0
              ? `（全市场 ${data.scan_stats.total_matches} 只大跌基金在其他板块）`
              : ""}
          </span>
          <button
            type="button"
            className="font-semibold text-[var(--brand-strong)] underline"
            onClick={() => onSectorFilterChange(null)}
          >
            查看全部
          </button>
        </div>
      ) : null}

      {leaders.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={`badge ${sectorFilter == null ? "bg-[var(--brand-soft)] text-[var(--brand-strong)]" : ""}`}
            onClick={() => onSectorFilterChange(null)}
          >
            全部
          </button>
          {leaders.map((leader) => (
            <button
              key={leader.sector_label}
              type="button"
              className={`badge ${sectorFilter === leader.sector_label ? "bg-[var(--brand-soft)] text-[var(--brand-strong)]" : ""}`}
              onClick={() =>
                onSectorFilterChange(
                  sectorFilter === leader.sector_label ? null : leader.sector_label,
                )
              }
            >
              {leader.sector_label}
              {leader.min_dip_drop_percent != null
                ? ` ${formatDipPercent(leader.min_dip_drop_percent)}`
                : ""}
            </button>
          ))}
        </div>
      ) : null}

      <p className="text-xs text-slate-400">{formatDipRadarUpdatedFromIso(data?.refreshed_at)}</p>

      {loading && !isDipRadarUsable(data) ? (
        <div className="empty-state py-10">
          <Loader2 className="mx-auto h-6 w-6 animate-spin text-slate-400" />
          <p className="mt-2 text-sm text-slate-500">扫描大跌基金…</p>
        </div>
      ) : null}

      {showEmpty ? (
        <div className="empty-state py-10">
          <p className="text-sm text-slate-600">{data?.message ?? "暂无符合跌幅阈值的基金"}</p>
          {data?.scan_stats ? (
            <p className="mt-2 text-xs text-slate-400">
              {data.scan_stats.sector_filter ? (
                <>
                  全市场扫描命中 {data.scan_stats.total_matches ?? data.scan_stats.matches ?? 0} 只
                  {typeof data.scan_stats.matches === "number"
                    ? ` · 「${data.scan_stats.sector_filter}」命中 ${data.scan_stats.matches} 只`
                    : null}
                </>
              ) : (
                <>
                  扫描：近1周跌幅短名单 {data.scan_stats.rank_shortlist ?? "—"} 只 · 门槛 ≥
                  {data.scan_stats.dip_threshold_percent ?? "—"}% · 命中 {data.scan_stats.matches ?? 0} 只
                </>
              )}
            </p>
          ) : null}
          <p className="mt-2 text-xs text-slate-400">
            可切换 3 日回看、点击刷新，或稍后在交易日盘中再试
          </p>
        </div>
      ) : null}

      {items.length > 0 ? (
        <div className="section-card overflow-hidden">
          <ul className="divide-y divide-[var(--line)]">
            {items.map((item) => (
              <li key={item.fund_code} className="dip-radar-row px-4 py-3.5 sm:px-5">
                <div className="flex items-start justify-between gap-3">
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => onOpenFund?.(item.fund_code, item.fund_name)}
                  >
                    <div className="text-sm font-semibold leading-snug text-slate-900">{item.fund_name}</div>
                    <div className="mt-1 text-xs text-slate-500">
                      {item.fund_code}
                      {item.sector_label ? ` · ${item.sector_label}` : ""}
                    </div>
                  </button>
                  <div className="shrink-0 text-right">
                    <div
                      className={`font-display text-base font-extrabold tabular-nums ${profitToneClass(item.dip_drop_percent)}`}
                    >
                      {formatDipPercent(item.dip_drop_percent)}
                    </div>
                    <div className="mt-0.5 text-[10px] font-semibold text-slate-400">近 {lookbackDays} 日</div>
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-2.5 border-t border-slate-100/80 pt-3">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                    <span className="text-slate-500">
                      信号分{" "}
                      <span className={`font-bold tabular-nums ${reboundScoreTone(item.rebound_score)}`}>
                        {formatReboundScore(item.rebound_score)}
                      </span>
                    </span>
                    <span className="text-slate-500">
                      反弹信号{" "}
                      <span className="font-medium text-slate-700">
                        {formatReboundSignals(item.rebound_signals)}
                      </span>
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn-secondary shrink-0 px-3 py-1.5 text-xs"
                    disabled={pendingSector === item.sector_label}
                    onClick={() => {
                      if (!item.sector_label) {
                        return;
                      }
                      setPendingSector(item.sector_label);
                      openDipSwingDiscovery(item.sector_label);
                      window.setTimeout(() => setPendingSector(null), 600);
                    }}
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    深度扫描
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="text-center text-[11px] leading-relaxed text-slate-400">{DIP_RADAR_DISCLAIMER}</p>
    </div>
  );
}
