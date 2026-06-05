import type { FundProfile, Holding, SectorQuoteMeta } from "@/lib/api";
import { isEstimateFallbackMeta } from "@/lib/sectorQuoteStatus";

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

/** 与后端 sector_quote_lookup_label 一致：有场内指数用指数名，否则用关联板块名 */
export function sectorQuoteLookupLabel(
  holding: Pick<Holding, "sector_name" | "intraday_index_name">,
): string | null {
  const indexName = holding.intraday_index_name?.trim();
  if (indexName && !isInvalidSectorLabel(indexName)) {
    return indexName;
  }
  const boardName = holding.sector_name?.trim();
  if (boardName && !isInvalidSectorLabel(boardName)) {
    return boardName;
  }
  return null;
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

export type IntradayQuery = {
  source_type: "index" | "concept" | "industry";
  source_name: string;
};

/** 关联板块短名 → 东财 zz 指数分时（与 apps/api sector_canonical 一致） */
const BOARD_TO_INTRADAY_INDEX: Record<string, string> = {
  半导体: "中证半导体",
  电网设备: "中证电网设备",
  人工智能: "中证人工智能",
};

function intradayIndexForBoard(boardName: string | null | undefined): string | null {
  const trimmed = boardName?.trim();
  if (!trimmed || isInvalidSectorLabel(trimmed)) {
    return null;
  }
  return BOARD_TO_INTRADAY_INDEX[trimmed] ?? null;
}

/** 详情弹窗分时图：有场内指数则走指数 K 线（008586→中证人工智能），概念板块名无分钟线 */
export function resolveIntradayQuery(
  holding: Pick<Holding, "fund_name" | "sector_name" | "intraday_index_name">,
  sectorMeta?: SectorQuoteMeta | null,
): IntradayQuery | null {
  const indexName = holding.intraday_index_name?.trim();
  if (indexName && !isInvalidSectorLabel(indexName)) {
    return { source_type: "index", source_name: indexName };
  }

  const boardIndex = intradayIndexForBoard(holding.sector_name);
  if (boardIndex) {
    return { source_type: "index", source_name: boardIndex };
  }

  const metaName = sectorMeta?.matched_name?.trim();
  const metaType = sectorMeta?.source_type;
  const fundHint = (holding.fund_name || "").trim();
  const metaLooksLikeFund =
    Boolean(metaName) &&
    Boolean(fundHint) &&
    (metaName === fundHint ||
      metaName!.includes("ETF") ||
      metaName!.includes("联接") ||
      metaName!.includes("发起"));

  if (
    metaType &&
    metaName &&
    !isInvalidSectorLabel(metaName) &&
    !isEstimateFallbackMeta(sectorMeta) &&
    !metaLooksLikeFund &&
    metaType !== "concept"
  ) {
    const mappedIndex = intradayIndexForBoard(metaName);
    if (mappedIndex) {
      return { source_type: "index", source_name: mappedIndex };
    }
    return { source_type: metaType, source_name: metaName };
  }

  const label = sectorQuoteLookupLabel(holding);
  if (!label) {
    return null;
  }

  const boardName = holding.sector_name?.trim();
  if (boardName && !isInvalidSectorLabel(boardName)) {
    const mappedIndex = intradayIndexForBoard(boardName);
    if (mappedIndex) {
      return { source_type: "index", source_name: mappedIndex };
    }
    return { source_type: "concept", source_name: boardName };
  }

  return { source_type: "index", source_name: label };
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
