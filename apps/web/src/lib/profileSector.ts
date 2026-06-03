import type { FundProfile, Holding } from "@/lib/api";

/** 档案/持仓展示用：关联板块短名 */
export function profileRelatedBoardLabel(
  profile: Pick<FundProfile, "sector_name" | "intraday_index_name">,
): string {
  if (profile.sector_name?.trim() && !isInvalidSectorLabel(profile.sector_name)) {
    return profile.sector_name;
  }
  if (profile.intraday_index_name?.trim()) {
    return profile.intraday_index_name;
  }
  return "—";
}

export function holdingRelatedBoardLabel(
  holding: Pick<Holding, "sector_name" | "intraday_index_name">,
): string {
  if (holding.sector_name?.trim() && !isInvalidSectorLabel(holding.sector_name)) {
    return holding.sector_name;
  }
  if (holding.intraday_index_name?.trim()) {
    return holding.intraday_index_name;
  }
  return "—";
}

export function isInvalidSectorLabel(name: string | null | undefined): boolean {
  if (!name) {
    return true;
  }
  const trimmed = name.trim();
  if (trimmed === "+" || trimmed === "-" || trimmed === "—") {
    return true;
  }
  if (trimmed === "关联板块" || trimmed === "场内指数") {
    return true;
  }
  return !/[\u4e00-\u9fff]/.test(trimmed);
}

export function showIntradayIndexHint(
  profile: Pick<FundProfile, "sector_name" | "intraday_index_name">,
): boolean {
  const board = profile.sector_name?.trim();
  const index = profile.intraday_index_name?.trim();
  return Boolean(
    board &&
      index &&
      !isInvalidSectorLabel(board) &&
      board !== index,
  );
}
