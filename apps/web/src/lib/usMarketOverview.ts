import type { UsMarketSnapshot, UsSessionKind } from "@/lib/api";

// 美股概览复用行情页既有子 Tab 联合类型（canonical 定义在 marketThemeBoard.ts）。
// 此处重新导出，便于美股相关模块就近引用。
export type { MarketSubTab } from "@/lib/marketThemeBoard";

export const US_INDEX_LIVE_REFRESH_INTERVAL_MS = 1_200_000;

/** 盘前 / 盘中 / 盘后才轮询；休市返回 null，等开盘再拉。 */
export function usRefreshIntervalMs(kind: UsSessionKind | null | undefined): number | null {
  const live = kind === "pre_market" || kind === "regular" || kind === "after_hours";
  return live ? US_INDEX_LIVE_REFRESH_INTERVAL_MS : null;
}

/** US_Session_Kind → 中文时段标签。 */
export const US_SESSION_LABEL: Record<UsSessionKind, string> = {
  pre_market: "盘前交易中",
  regular: "盘中",
  after_hours: "盘后",
  closed: "休市",
};

const US_TZ = "America/New_York";

function parseSnapshotTime(updatedAt: string | null | undefined): Date | null {
  if (!updatedAt) {
    return null;
  }
  const parsed = new Date(updatedAt);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** 快照采集时刻的美东墙钟日期+小时：`2026-08-16 02时 ET`。 */
export function formatUsEtClock(at: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: US_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
  }).formatToParts(at);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  const year = value("year");
  const month = value("month");
  const day = value("day");
  const hour = value("hour");
  if (!year || !month || !day || !hour) {
    return "";
  }
  return `${year}-${month}-${day} ${hour}时 ET`;
}

export function formatUsIndexCaption(
  data: {
    session_kind?: string | null;
    session_label?: string | null;
    updated_at?: string | null;
    et_date?: string | null;
  } | null | undefined,
): string | null {
  if (!data) {
    return null;
  }
  const sessionLabel = data.session_kind
    ? (US_SESSION_LABEL[data.session_kind as UsSessionKind] ?? data.session_label ?? null)
    : (data.session_label ?? null);
  const collectedAt = parseSnapshotTime(data.updated_at);
  const clock = collectedAt
    ? formatUsEtClock(collectedAt)
    : data.et_date
      ? `${data.et_date} ET`
      : "";
  return [sessionLabel, clock].filter(Boolean).join(" · ") || null;
}

/**
 * stale-while-revalidate：仅当新快照 `available` 为真时才用其替换旧数据，
 * 否则保留上一份可用快照（与 `acceptMarketThemeBoardFresh` 等
 * `keepPreviousUnless` 谓词风格一致，配合 `useCachedFetch` 使用）。
 */
export function acceptUsMarketFresh(fresh: UsMarketSnapshot): boolean {
  return Boolean(fresh?.available);
}
