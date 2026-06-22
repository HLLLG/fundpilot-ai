import type { FundProfile, Holding, SectorQuoteMeta } from "@/lib/api";
import { isEstimateFallbackMeta } from "@/lib/sectorQuoteStatus";

const FUND_NAME_TOPIC_TOKENS = [
  "国防军工",
  "商业航天",
  "人工智能",
  "电网设备",
  "半导体",
  "新能源",
  "红利",
  "传媒",
  "CPO",
] as const;

const FUND_PRODUCT_LABEL_RE =
  /(?:混合|联接|链接|发起|精选|股票)[A-CEH]?$|(?:混合|联接|链接|发起|精选|ETF|LOF)/i;

/** 与 apps/api sector_canonical 一致的英文/数字板块短名 */
const CANONICAL_ASCII_SECTOR_LABELS = new Set(["CPO", "PCB", "5G"]);

/** 与 apps/api GLOBAL_FUND_SECTOR_SEEDS 同步 */
const FUND_CODE_SECTOR_SEEDS: Record<string, { sector_name: string; intraday_index_name?: string }> = {
  "018957": { sector_name: "CPO" },
  "010236": { sector_name: "传媒", intraday_index_name: "传媒" },
};

function seededSectorFields(
  holding: Pick<Holding, "fund_code" | "sector_name" | "intraday_index_name">,
): { sector_name: string; intraday_index_name?: string } | null {
  const code = (holding.fund_code || "").trim().padStart(6, "0");
  if (!code || code === "000000") {
    return null;
  }
  const seed = FUND_CODE_SECTOR_SEEDS[code];
  if (!seed) {
    return null;
  }
  const sectorName =
    holding.sector_name?.trim() && !isInvalidSectorLabel(holding.sector_name)
      ? holding.sector_name.trim()
      : seed.sector_name;
  const intradayIndex =
    holding.intraday_index_name?.trim() && !isInvalidSectorLabel(holding.intraday_index_name)
      ? holding.intraday_index_name.trim()
      : seed.intraday_index_name;
  return {
    sector_name: sectorName,
    ...(intradayIndex ? { intraday_index_name: intradayIndex } : {}),
  };
}

function seededSectorLabel(
  holding: Pick<Holding, "fund_code" | "sector_name">,
): string | null {
  const seeded = seededSectorFields(holding);
  if (!seeded || isInvalidSectorLabel(seeded.sector_name)) {
    return null;
  }
  return seeded.sector_name;
}

export function inferSectorLabelFromFundName(fundName: string | null | undefined): string | null {
  const normalized = (fundName || "").replace("...", "").replace(/\s+/g, "");
  if (!normalized) {
    return null;
  }
  for (const token of FUND_NAME_TOPIC_TOKENS) {
    if (normalized.includes(token)) {
      return token;
    }
  }
  return null;
}

/** 持仓列表「板块」列展示名：档案/OCR → 基金名推断 → 估值兜底提示 */
export function holdingDisplaySectorLabel(
  holding: Pick<Holding, "fund_code" | "fund_name" | "sector_name" | "intraday_index_name">,
  sectorMeta?: SectorQuoteMeta | null,
): string {
  const base = holdingRelatedBoardLabel(holding);
  if (base !== "—") {
    return base;
  }
  const seeded = seededSectorLabel(holding);
  if (seeded) {
    return seeded;
  }
  const inferred = inferSectorLabelFromFundName(holding.fund_name);
  if (inferred) {
    return inferred;
  }
  if (isEstimateFallbackMeta(sectorMeta)) {
    return "基金估值";
  }
  return "—";
}

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

const FUND_NAME_INDEX_TOKENS = [
  "中证电网设备",
  "中证人工智能",
  "中证半导体",
  "中证新能源",
  "中证军工",
] as const;

const FEEDER_THEME_TO_INDEX: Record<string, string> = {
  人工智能: "中证人工智能",
  电网设备: "中证电网设备",
  半导体: "中证半导体",
  新能源: "中证新能源",
  军工: "中证军工",
};

function inferIndexFromFundName(fundName: string | null | undefined): string | null {
  const normalized = (fundName || "").replace("...", "").trim();
  if (!normalized) {
    return null;
  }
  for (const token of FUND_NAME_INDEX_TOKENS) {
    if (normalized.includes(token)) {
      return token;
    }
  }
  const compact = normalized.replace(/\s+/g, "");
  if (!compact.includes("ETF联接") && !compact.includes("ETF连接")) {
    return null;
  }
  for (const [theme, index] of Object.entries(FEEDER_THEME_TO_INDEX)) {
    if (normalized.includes(theme)) {
      return index;
    }
  }
  return null;
}

/** 与后端 sector_quote_lookup_label 一致：ETF 联接 / OCR 场内指数 → 指数；否则关联板块短名 */
export function sectorQuoteLookupLabel(
  holding: Pick<Holding, "fund_code" | "fund_name" | "sector_name" | "intraday_index_name">,
): string | null {
  const fromFund = inferIndexFromFundName(holding.fund_name);
  if (fromFund) {
    return fromFund;
  }
  const seeded = seededSectorFields(holding);
  if (seeded?.sector_name && !isInvalidSectorLabel(seeded.sector_name)) {
    return seeded.sector_name;
  }
  const boardName = holding.sector_name?.trim();
  if (boardName && !isInvalidSectorLabel(boardName)) {
    return boardName;
  }
  const indexName = holding.intraday_index_name?.trim();
  if (indexName && !isInvalidSectorLabel(indexName)) {
    return indexName;
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
  if (!/[\u4e00-\u9fff]/.test(trimmed)) {
    return !CANONICAL_ASCII_SECTOR_LABELS.has(trimmed.toUpperCase());
  }
  const compact = trimmed.replace(/\s+/g, "");
  if (FUND_PRODUCT_LABEL_RE.test(compact)) {
    return true;
  }
  if (compact.length > 8 && /(?:混合|联接|链接|发起|精选|ETF|LOF)/.test(compact)) {
    return true;
  }
  return false;
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
  传媒: "传媒",
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
  holding: Pick<Holding, "fund_code" | "fund_name" | "sector_name" | "intraday_index_name">,
  sectorMeta?: SectorQuoteMeta | null,
): IntradayQuery | null {
  const seeded = seededSectorFields(holding);
  const effectiveHolding = seeded
    ? {
        ...holding,
        sector_name: seeded.sector_name,
        intraday_index_name: seeded.intraday_index_name ?? holding.intraday_index_name,
      }
    : holding;

  const indexName = effectiveHolding.intraday_index_name?.trim();
  if (indexName && !isInvalidSectorLabel(indexName)) {
    return { source_type: "index", source_name: indexName };
  }

  const boardIndex = intradayIndexForBoard(effectiveHolding.sector_name);
  if (boardIndex) {
    return { source_type: "index", source_name: boardIndex };
  }

  const metaName = sectorMeta?.matched_name?.trim();
  const metaType = sectorMeta?.source_type;
  const fundHint = (effectiveHolding.fund_name || "").trim();
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

  const label = sectorQuoteLookupLabel(effectiveHolding);
  if (!label) {
    return null;
  }

  const boardName = effectiveHolding.sector_name?.trim();
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
