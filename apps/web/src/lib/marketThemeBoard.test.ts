import { describe, expect, it } from "vitest";

import {
  boardKindClass,
  formatBoardKindLabel,
  formatThemeBoardUpdatedAt,
  formatThemeBoardUpdatedFromIso,
  formatThemeFlowYi,
  formatThemePercent,
  formatThemeRank,
  hasThemeFlowDetail,
  nextThemeSortState,
  sortThemeBoardItems,
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

  it("formats flow yi with sign", () => {
    expect(formatThemeFlowYi(12.34)).toBe("+12.34亿");
    expect(formatThemeFlowYi(-7.5)).toBe("-7.50亿");
    expect(formatThemeFlowYi(null)).toBe("—");
  });

  it("detects expandable flow detail", () => {
    expect(
      hasThemeFlowDetail({
        main_force_net_yi: 1.2,
        flow_tiers: { super_large_net_yi: 2.0 },
      }),
    ).toBe(true);
    expect(hasThemeFlowDetail({ main_force_net_yi: null, flow_tiers: null })).toBe(false);
  });

  it("sorts theme board items by column and direction", () => {
    const items = [
      { sector_label: "A", board_kind: "concept" as const, change_1d_percent: 1, main_force_net_yi: 10, held_fund_count: 0, in_portfolio: false },
      { sector_label: "B", board_kind: "concept" as const, change_1d_percent: 3, main_force_net_yi: -5, held_fund_count: 0, in_portfolio: false },
      { sector_label: "C", board_kind: "concept" as const, change_1d_percent: null, main_force_net_yi: 2, held_fund_count: 0, in_portfolio: false },
    ];
    const byChangeDesc = sortThemeBoardItems(items, "change", "desc");
    expect(byChangeDesc.map((item) => item.sector_label)).toEqual(["B", "A", "C"]);
    expect(byChangeDesc[0].rank).toBe(1);

    const byInflowAsc = sortThemeBoardItems(items, "inflow", "asc");
    expect(byInflowAsc.map((item) => item.sector_label)).toEqual(["B", "C", "A"]);
  });

  it("toggles sort direction on repeated column click", () => {
    expect(nextThemeSortState("change", "inflow", "desc")).toEqual({ column: "change", direction: "desc" });
    expect(nextThemeSortState("change", "change", "desc")).toEqual({ column: "change", direction: "asc" });
    expect(nextThemeSortState("change", "change", "asc")).toEqual({ column: "change", direction: "desc" });
  });
});
