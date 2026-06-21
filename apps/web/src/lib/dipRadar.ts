import type { DiscoveryScanMode, DipRadarResponse } from "@/lib/api";
import { saveDashboardTab } from "@/lib/storage";

export type {
  DipRadarItem,
  DipRadarReboundSignal,
  DipRadarResponse,
  DipRadarSectorLeader,
} from "@/lib/api";

export const DIP_RADAR_DISCLAIMER =
  "大跌雷达基于基金净值跌幅与板块信号筛选，仅供研究参考，不构成投资建议。历史反弹统计为板块代理口径，单基走势可能偏离。";

export function isDipRadarUsable(data: DipRadarResponse | null | undefined): boolean {
  return Boolean(data?.available && (data.items?.length ?? 0) > 0);
}

/** 空榜也是有效响应；仅拒绝缺少 refreshed_at 的残缺 payload。 */
export function acceptDipRadarFresh(fresh: DipRadarResponse): boolean {
  return Boolean(fresh.refreshed_at);
}

export function formatDipPercent(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

export function formatReboundScore(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  return String(Math.round(value));
}

export function reboundScoreTone(value: number | null | undefined): string {
  if (value == null) {
    return "text-slate-500";
  }
  if (value >= 70) {
    return "text-emerald-600";
  }
  if (value >= 45) {
    return "text-amber-600";
  }
  return "text-slate-500";
}

export function profitToneClass(value: number | null | undefined): string {
  if (value == null || value === 0) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

export function formatDipRadarUpdatedFromIso(iso: string | null | undefined): string {
  if (!iso) {
    return "等待扫描结果…";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "加载中…";
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return `更新于 ${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatReboundSignals(
  signals: Array<{ id: string; label: string }> | null | undefined,
): string {
  if (!signals?.length) {
    return "—";
  }
  return signals.map((signal) => signal.label).join(" · ");
}

const DISCOVERY_PREFILL_KEY = "fundpilot-discovery-prefill";
const DASHBOARD_TAB_EVENT = "fundpilot-dashboard-tab";

type DipDiscoveryPrefill = {
  scanMode: DiscoveryScanMode;
  focusSectors: string[];
};

export function openDipSwingDiscovery(sectorLabel: string): void {
  if (typeof window === "undefined") {
    return;
  }
  const prefill: DipDiscoveryPrefill = {
    scanMode: "dip_swing",
    focusSectors: [sectorLabel].slice(0, 3),
  };
  saveDashboardTab("discovery");
  window.sessionStorage.setItem(DISCOVERY_PREFILL_KEY, JSON.stringify(prefill));
  window.dispatchEvent(new CustomEvent(DASHBOARD_TAB_EVENT, { detail: "discovery" }));
}
