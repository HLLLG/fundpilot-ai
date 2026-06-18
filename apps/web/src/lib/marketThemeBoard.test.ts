import { describe, expect, it } from "vitest";

import {
  boardKindClass,
  formatBoardKindLabel,
  formatConsecutiveDays,
  formatThemeBoardUpdatedAt,
  formatThemeBoardUpdatedFromIso,
  formatThemePercent,
  formatThemeRank,
  themeBoardHeading,
  themeRankClass,
} from "@/lib/marketThemeBoard";

describe("marketThemeBoard formatters", () => {
  it("uses fixed gainers heading", () => {
    expect(themeBoardHeading()).toBe("今日板块涨幅榜");
  });

  it("formats rank with leading zero", () => {
    expect(formatThemeRank(1, 0)).toBe("01");
    expect(formatThemeRank(undefined, 7)).toBe("08");
  });

  it("highlights top three ranks", () => {
    expect(themeRankClass(1, 0)).toContain("amber");
    expect(themeRankClass(4, 3)).toContain("slate");
  });

  it("formats consecutive up days like xiaobei", () => {
    expect(formatConsecutiveDays(3)).toBe("+3天");
    expect(formatConsecutiveDays(0)).toBe("—");
    expect(formatConsecutiveDays(null)).toBe("—");
  });

  it("formats percent with sign", () => {
    expect(formatThemePercent(7.44)).toBe("+7.44%");
    expect(formatThemePercent(-1.2)).toBe("-1.20%");
  });

  it("formats updated timestamp", () => {
    const label = formatThemeBoardUpdatedAt(new Date(2026, 5, 18, 8, 12, 37));
    expect(label).toBe("更新于 06-18 08:12:37");
  });

  it("formats board kind labels", () => {
    expect(formatBoardKindLabel("industry")).toBe("行业");
    expect(formatBoardKindLabel("concept")).toBe("概念");
    expect(formatBoardKindLabel("index")).toBe("指数");
    expect(formatBoardKindLabel(undefined)).toBe("概念");
  });

  it("maps board kind to tone class", () => {
    expect(boardKindClass("industry")).toContain("slate");
    expect(boardKindClass("index")).toContain("violet");
    expect(boardKindClass("concept")).toContain("amber");
  });

  it("formats updated timestamp from iso, falling back when empty", () => {
    expect(formatThemeBoardUpdatedFromIso(null)).toBe("加载中…");
    expect(formatThemeBoardUpdatedFromIso("not-a-date")).toBe("加载中…");
    expect(formatThemeBoardUpdatedFromIso("2026-06-18T08:12:37").startsWith("更新于")).toBe(true);
  });
});
