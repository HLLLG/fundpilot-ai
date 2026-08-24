import type { Holding, SectorQuoteMeta } from "@/lib/api";

const FUND_NAME_TOPIC_TOKENS = [
  "国防军工",
  "商业航天",
  "人工智能",
  "电网设备",
  "半导体材料",
  "半导体",
  "新能源",
  "红利",
  "传媒",
  "CPO",
  "CXO",
  "医药",
  "医疗",
  "互联网",
  "军工",
  "煤炭",
  "黄金",
  "电子",
  "计算机",
] as const;

const FUND_PRODUCT_LABEL_RE =
  /(?:混合|联接|链接|发起|精选|股票)[A-CEH]?$|(?:混合|联接|链接|发起|精选|ETF|LOF)/i;

/** 与 apps/api sector_canonical 一致的英文/数字板块短名 */
const CANONICAL_ASCII_SECTOR_LABELS = new Set(["CPO", "CXO", "PCB", "5G"]);

/** 与 apps/api TRACKING_INDEX_DISPLAY 同步：合同跟踪指数的养基宝简称 */
const TRACKING_INDEX_DISPLAY_NAMES = new Set([
  "黄金9999",
  "黄金999",
  "房地产指数",
  "国证地产",
  "沪港深黄金",
  "沪深港黄金",
  "国证医药",
  "国证有色",
  "国证食品",
  "国证CXO",
]);

/** 与 apps/api GLOBAL_FUND_SECTOR_SEEDS 同步 */
/** 与后端 `_SECTOR_INTRADAY_INDEX_OVERRIDES` 同步：CXO 分时走国证CXO，不是 BK1600 */
const SECTOR_INTRADAY_INDEX_OVERRIDES: Record<string, string> = {
  CXO: "国证CXO",
};

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

function isPassiveIndexFundName(fundName: string | null | undefined): boolean {
  if (!fundName) {
    return false;
  }
  const normalized = fundName.toUpperCase();
  if (["指数", "ETF", "联接", "LOF"].some((marker) => normalized.includes(marker))) {
    return true;
  }
  const compact = fundName.replace(/\s+/g, "");
  return /黄金股[ACEH]?$/i.test(compact);
}

const UNDETERMINED_ASSOCIATED_SECTOR_MARKERS = [
  "灵活配置",
  "滚动持有",
  "宏观择时",
  "多策略",
  "量化对冲",
  "行业轮动",
  "风格轮动",
] as const;

/** 名称无板块主题、产品也不跟踪单一赛道：持仓页不展示猜测的关联板块。 */
export function isUnthemedAllocationFund(fundName: string | null | undefined): boolean {
  if (!fundName || isPassiveIndexFundName(fundName)) {
    return false;
  }
  const compact = fundName.replace(/\s+/g, "");
  if (["债券", "货币", "短债", "中短债", "纯债", "理财", "固收"].some((token) => compact.includes(token))) {
    return false;
  }
  if (inferSectorLabelFromFundName(fundName)) {
    return false;
  }
  return UNDETERMINED_ASSOCIATED_SECTOR_MARKERS.some((token) => compact.includes(token));
}

function inferSectorLabelFromFundName(fundName: string | null | undefined): string | null {
  const normalized = (fundName || "").replace("...", "").replace(/\s+/g, "");
  if (!normalized) {
    return null;
  }
  const tokens = [...FUND_NAME_TOPIC_TOKENS].sort((left, right) => right.length - left.length);
  for (const token of tokens) {
    if (normalized.includes(token)) {
      return token;
    }
  }
  return null;
}

const HEALTHCARE_PARENT_LOCKS = ["医药", "医疗"] as const;

function healthcareParentLockAgainstCxo(
  fundName: string | null | undefined,
): "医疗" | "医药" | null {
  const normalized = (fundName || "").replace("...", "").replace(/\s+/g, "");
  if (!normalized || /cxo/i.test(normalized)) {
    return null;
  }
  const inferred = inferSectorLabelFromFundName(fundName);
  if (inferred === "医疗" || inferred === "医药") {
    return inferred;
  }
  for (const token of HEALTHCARE_PARENT_LOCKS) {
    if (normalized.includes(token)) {
      return token;
    }
  }
  return null;
}

function applyHealthcareParentLock<
  T extends Pick<Holding, "fund_name" | "sector_name" | "intraday_index_name">,
>(holding: T): T {
  const locked = healthcareParentLockAgainstCxo(holding.fund_name);
  if (!locked) {
    return holding;
  }
  const sector = holding.sector_name?.trim() || "";
  const index = holding.intraday_index_name?.trim() || "";
  if (sector !== "CXO" && index !== "国证CXO") {
    return holding;
  }
  return {
    ...holding,
    sector_name: sector === "CXO" ? locked : holding.sector_name,
    intraday_index_name: index === "国证CXO" ? null : holding.intraday_index_name,
  };
}

/** 持仓列表「板块」列展示名：档案/OCR → 基金名推断 */
export function holdingDisplaySectorLabel(
  holding: Pick<Holding, "fund_code" | "fund_name" | "sector_name" | "intraday_index_name">,
): string {
  if (isUnthemedAllocationFund(holding.fund_name)) {
    return "—";
  }
  const remapped = applyHealthcareParentLock(holding);
  const base = holdingRelatedBoardLabel(remapped);
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
  return "—";
}

function canonicalTrackingDisplayName(name: string | null | undefined): string | null {
  const trimmed = name?.trim() || "";
  if (!trimmed) {
    return null;
  }
  return trimmed === "黄金999" ? "黄金9999" : trimmed;
}

function fundLooksLikeIndexOrFeeder(fundName: string | null | undefined): boolean {
  if (inferIndexFromFundName(fundName)) {
    return true;
  }
  const compact = (fundName || "").replace(/\s+/g, "");
  return /指数|ETF|联接|连接|LOF/i.test(compact);
}

function holdingRelatedBoardLabel(
  holding: Pick<Holding, "fund_name" | "sector_name" | "intraday_index_name">,
): string {
  const indexName = canonicalTrackingDisplayName(holding.intraday_index_name);
  const fromFund = canonicalTrackingDisplayName(inferIndexFromFundName(holding.fund_name));
  const trackingIndex =
    indexName &&
    TRACKING_INDEX_DISPLAY_NAMES.has(indexName) &&
    !isInvalidSectorLabel(indexName)
      ? indexName
      : fromFund &&
          TRACKING_INDEX_DISPLAY_NAMES.has(fromFund) &&
          !isInvalidSectorLabel(fromFund)
        ? fromFund
        : null;
  // 指数/联接跟合同标的，展示场内指数；主动基金展示板块身份。
  // 否则 CXO 的分时代理「国证CXO」会盖掉关联板块。
  if (trackingIndex && fundLooksLikeIndexOrFeeder(holding.fund_name)) {
    return trackingIndex;
  }
  if (holding.sector_name?.trim() && !isInvalidSectorLabel(holding.sector_name)) {
    return holding.sector_name;
  }
  if (trackingIndex) {
    return trackingIndex;
  }
  if (indexName) {
    return indexName;
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
  半导体材料: "半导体材料",
  半导体: "中证半导体",
  新能源: "中证新能源",
  军工: "中证军工",
  黄金: "黄金9999",
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
  const themes = Object.entries(FEEDER_THEME_TO_INDEX).sort(
    (left, right) => right[0].length - left[0].length,
  );
  for (const [theme, index] of themes) {
    if (normalized.includes(theme)) {
      return index;
    }
  }
  return null;
}

/** 与后端 sector_quote_lookup_label 一致：ETF 联接 / OCR 场内指数 → 指数；否则关联板块短名 */
function sectorQuoteLookupLabel(
  holding: Pick<Holding, "fund_code" | "fund_name" | "sector_name" | "intraday_index_name">,
): string | null {
  const fromFund = canonicalTrackingDisplayName(inferIndexFromFundName(holding.fund_name));
  if (fromFund) {
    return fromFund;
  }
  const seeded = seededSectorFields(holding);
  if (seeded?.sector_name && !isInvalidSectorLabel(seeded.sector_name)) {
    return seeded.sector_name;
  }
  const indexName = canonicalTrackingDisplayName(holding.intraday_index_name);
  if (
    indexName &&
    TRACKING_INDEX_DISPLAY_NAMES.has(indexName) &&
    !isInvalidSectorLabel(indexName)
  ) {
    return indexName;
  }
  const boardName = holding.sector_name?.trim();
  if (boardName && !isInvalidSectorLabel(boardName)) {
    return boardName;
  }
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

/**
 * 详情弹窗只提交基金档案里的指数名或关联板块标签；具体 secid 与指数/概念类型
 * 由 API 的 sector registry 统一解析，前端不再维护一份容易漂移的板块映射。
 */
export function resolveIntradayQuery(
  holding: Pick<Holding, "fund_code" | "fund_name" | "sector_name" | "intraday_index_name">,
  sectorMeta?: SectorQuoteMeta | null,
): IntradayQuery | null {
  if (isUnthemedAllocationFund(holding.fund_name)) {
    return null;
  }
  const remapped = applyHealthcareParentLock(holding);
  const seeded = seededSectorFields(remapped);
  const effectiveHolding = seeded
    ? {
        ...remapped,
        sector_name: seeded.sector_name,
        intraday_index_name: seeded.intraday_index_name ?? remapped.intraday_index_name,
      }
    : remapped;

  const indexName = canonicalTrackingDisplayName(effectiveHolding.intraday_index_name);
  if (indexName && !isInvalidSectorLabel(indexName)) {
    return { source_type: "index", source_name: indexName };
  }

  const boardOverride =
    SECTOR_INTRADAY_INDEX_OVERRIDES[effectiveHolding.sector_name?.trim() || ""];
  if (boardOverride) {
    return { source_type: "index", source_name: boardOverride };
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
    !metaLooksLikeFund &&
    metaType !== "concept"
  ) {
    return { source_type: metaType, source_name: metaName };
  }

  const label = canonicalTrackingDisplayName(sectorQuoteLookupLabel(effectiveHolding));
  if (!label) {
    return null;
  }
  if (TRACKING_INDEX_DISPLAY_NAMES.has(label) && !isInvalidSectorLabel(label)) {
    return { source_type: "index", source_name: label };
  }

  const boardName = effectiveHolding.sector_name?.trim();
  if (boardName && !isInvalidSectorLabel(boardName)) {
    // source_type 是兼容旧 API 的查询提示；后端会按 registry 解析成真实指数/
    // 概念/行业身份，因此这里无需知道“互联网→930604”等具体映射。
    return { source_type: "concept", source_name: boardName };
  }

  return { source_type: "index", source_name: label };
}

/**
 * 分时图兜底查询：业绩基准原文抠出来的场内指数名（如"中证高端装备制造指数"）经常
 * 不在行情源的别名表里，查不到分时会一直显示"暂无分时数据"；而"关联板块"短名
 * （如"机械设备"）往往已经注册过行情源。主查询查不到数据时，用这个兜底按板块短名
 * 再试一次，而不是要求持续扩充指数名别名表。
 */
export function resolveIntradayFallbackQuery(
  holding: Pick<Holding, "fund_code" | "fund_name" | "sector_name" | "intraday_index_name">,
  primaryQuery: IntradayQuery | null,
): IntradayQuery | null {
  if (isUnthemedAllocationFund(holding.fund_name)) {
    return null;
  }
  const remapped = applyHealthcareParentLock(holding);
  const seeded = seededSectorFields(remapped);
  const boardName = (seeded?.sector_name ?? remapped.sector_name)?.trim();
  if (!boardName || isInvalidSectorLabel(boardName)) {
    return null;
  }
  const fallback: IntradayQuery = { source_type: "concept", source_name: boardName };
  if (
    primaryQuery &&
    primaryQuery.source_type === fallback.source_type &&
    primaryQuery.source_name === fallback.source_name
  ) {
    return null;
  }
  return fallback;
}
