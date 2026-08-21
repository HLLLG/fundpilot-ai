import { describe, expect, it } from "vitest";
import {
  holdingDisplaySectorLabel,
  isInvalidSectorLabel,
  resolveIntradayFallbackQuery,
  resolveIntradayQuery,
} from "@/lib/profileSector";

describe("profileSector", () => {
  it("accepts canonical ascii sector labels like CPO", () => {
    expect(isInvalidSectorLabel("CPO")).toBe(false);
    expect(isInvalidSectorLabel("CXO")).toBe(false);
    expect(isInvalidSectorLabel("国证CXO")).toBe(false);
    expect(isInvalidSectorLabel("中航机遇领航混合发起C")).toBe(true);
  });

  it("resolves intraday from fund code seed when sector_name is missing", () => {
    const holding = {
      fund_code: "018957",
      fund_name: "中航机遇领航混合发起C",
      sector_name: null,
      intraday_index_name: null,
    };
    expect(holdingDisplaySectorLabel(holding)).toBe("CPO");
    expect(resolveIntradayQuery(holding)).toEqual({
      source_type: "concept",
      source_name: "CPO",
    });
  });

  it("falls back to sector_name based query when benchmark-derived index name is unusable", () => {
    // 回归测试："天弘全球高端制造混合(QDII)C" 的场内指数名是业绩基准原文抠出来的
    // "中证高端装备制造指数"，行情源查不到分时；关联板块短名"机械设备"已经注册过
    // 行情源，应该作为兜底查询提供，而不是要求持续扩充指数名别名表。
    const holding = {
      fund_code: "016665",
      fund_name: "天弘全球高端制造混合(QDII)C",
      sector_name: "机械设备",
      intraday_index_name: "中证高端装备制造指数",
    };
    const primary = resolveIntradayQuery(holding);
    expect(primary).toEqual({
      source_type: "index",
      source_name: "中证高端装备制造指数",
    });
    const fallback = resolveIntradayFallbackQuery(holding, primary);
    expect(fallback).toEqual({ source_type: "concept", source_name: "机械设备" });
  });

  it("returns no fallback when primary query already targets the board name", () => {
    const holding = {
      fund_code: "999999",
      fund_name: "某某电网设备主题ETF联接C",
      sector_name: "电网设备",
      intraday_index_name: null,
    };
    const primary = resolveIntradayQuery(holding);
    expect(primary).toEqual({ source_type: "concept", source_name: "电网设备" });
    expect(resolveIntradayFallbackQuery(holding, primary)).toBeNull();
  });

  it("delegates registered board identity resolution to the API registry", () => {
    const holding = {
      fund_code: "006751",
      fund_name: "富国互联科技股票A",
      sector_name: "互联网",
      intraday_index_name: null,
    };

    expect(resolveIntradayQuery(holding)).toEqual({
      source_type: "concept",
      source_name: "互联网",
    });
  });

  it("prefers tracking-index short names over theme board labels", () => {
    expect(
      holdingDisplaySectorLabel({
        fund_code: "160218",
        fund_name: "国泰国证房地产行业指数A",
        sector_name: "房地产",
        intraday_index_name: "房地产指数",
      }),
    ).toBe("房地产指数");
    expect(
      holdingDisplaySectorLabel({
        fund_code: "002610",
        fund_name: "博时黄金ETF联接A",
        sector_name: "黄金",
        intraday_index_name: "黄金999",
      }),
    ).toBe("黄金9999");
    expect(
      holdingDisplaySectorLabel({
        fund_code: "002610",
        fund_name: "博时黄金ETF联接A",
        sector_name: "黄金",
        intraday_index_name: null,
      }),
    ).toBe("黄金9999");
    expect(
      resolveIntradayQuery({
        fund_code: "002610",
        fund_name: "博时黄金ETF联接A",
        sector_name: "黄金",
        intraday_index_name: null,
      }),
    ).toEqual({
      source_type: "index",
      source_name: "黄金9999",
    });
    expect(
      holdingDisplaySectorLabel({
        fund_code: "021959",
        fund_name: "南方黄金股C",
        sector_name: "黄金股",
        intraday_index_name: "沪港深黄金",
      }),
    ).toBe("沪港深黄金");
  });

  it("charts 国证CXO when the related board is CXO", () => {
    const holding = {
      fund_code: "000960",
      fund_name: "招商医疗保健股票A",
      sector_name: "CXO",
      intraday_index_name: null,
    };
    expect(resolveIntradayQuery(holding)).toEqual({
      source_type: "index",
      source_name: "国证CXO",
    });
    expect(
      holdingDisplaySectorLabel({
        ...holding,
        intraday_index_name: "国证CXO",
      }),
    ).toBe("国证CXO");
  });
});
