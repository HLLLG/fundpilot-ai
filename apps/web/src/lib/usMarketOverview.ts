import type { UsMarketSnapshot, UsSessionKind } from "@/lib/api";

// 美股子 Tab 复用「市场」Tab 既有子 Tab 联合类型（canonical 定义在 marketThemeBoard.ts，
// 已包含 "us"）。此处重新导出，便于美股相关模块就近引用。
export type { MarketSubTab } from "@/lib/marketThemeBoard";

/**
 * 时段感知自动刷新间隔（毫秒）。
 * 盘前 / 盘中（高频）→ 45s；盘后 / 休市（低频）→ 300s（5min）。
 */
export function usRefreshIntervalMs(kind: UsSessionKind): number {
  return kind === "pre_market" || kind === "regular" ? 45_000 : 300_000;
}

/** US_Session_Kind → 中文时段标签。 */
export const US_SESSION_LABEL: Record<UsSessionKind, string> = {
  pre_market: "盘前交易中",
  regular: "盘中",
  after_hours: "盘后",
  closed: "休市",
};

/**
 * stale-while-revalidate：仅当新快照 `available` 为真时才用其替换旧数据，
 * 否则保留上一份可用快照（与 `acceptMarketThemeBoardFresh` 等
 * `keepPreviousUnless` 谓词风格一致，配合 `useCachedFetch` 使用）。
 */
export function acceptUsMarketFresh(fresh: UsMarketSnapshot): boolean {
  return Boolean(fresh?.available);
}
