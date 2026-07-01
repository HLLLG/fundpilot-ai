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
    expect(primary).toEqual({ source_type: "index", source_name: "中证电网设备" });
    expect(resolveIntradayFallbackQuery(holding, primary)).toBeNull();
  });
});
