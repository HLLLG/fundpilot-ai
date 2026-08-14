/** 与后端 `normalize_fund_name_for_lookup` 对齐：支付宝全称 ↔ 东财简称。 */

const LOOKUP_STRIP_TOKENS = ["发起式", "主题", "灵活配置", "证券投资基金"] as const;

export function normalizeFundNameForLookup(name: string): string {
  let result = name
    .replaceAll("...", "")
    .replaceAll("..", "")
    .replaceAll(".", "")
    .replaceAll("·", "")
    .replaceAll(" ", "")
    .trim();
  result = result.replace(/ETF联([A-CEH])$/i, "ETF联接$1");
  result = result.replaceAll("（", "(").replaceAll("）", ")");
  for (const token of LOOKUP_STRIP_TOKENS) {
    result = result.replaceAll(token, "");
  }
  result = result.replace(/混合发起([A-CEH])$/i, "混合$1");
  result = result.replace(/ETF发起联接/gi, "ETF联接");
  result = result.replace(/发起联接/g, "联接");
  result = result.replaceAll("上证科创板", "科创");
  result = result.replaceAll("半导体材料设备", "半导体设备");
  result = result.replace(/(混合|股票|指数|债券|联接)型/g, "$1");
  return result;
}

export function extractShareClassLetter(name: string): string | null {
  const normalized = normalizeFundNameForLookup(name);
  const match = normalized.match(
    /(?:混合|联接|ETF联接|ETF联|股票|指数)(?:[（(](?:QDII|LOF|FOF|QDII-ETF)[)）])?(?:人民币|美元|港币)?([A-CEH])$/i,
  );
  return match?.[1]?.toUpperCase() ?? null;
}

export function pickUniqueFundMatch<T extends { fund_code: string; fund_name: string }>(
  query: string,
  items: T[],
): T | null {
  const target = normalizeFundNameForLookup(query);
  if (!target) {
    return null;
  }
  const targetClass = extractShareClassLetter(query);
  const exact = items.filter((item) => {
    if (!item.fund_code || item.fund_code === "000000") {
      return false;
    }
    if (normalizeFundNameForLookup(item.fund_name) !== target) {
      return false;
    }
    const itemClass = extractShareClassLetter(item.fund_name);
    return !targetClass || !itemClass || targetClass === itemClass;
  });
  return exact.length === 1 ? exact[0] : null;
}
