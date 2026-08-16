import { describe, expect, it } from "vitest";

import {
  boardKindClass,
  countHeldThemeBoards,
  filterThemeBoardItems,
  formatBoardKindLabel,
  formatThemeBoardUpdatedAt,
  formatThemeFlowYi,
  formatThemePercent,
  formatThemeRank,
  formatThemeStreak,
  hasThemeFlowDetail,
  nextThemeSortState,
  sortThemeBoardItems,
  themeBoardHeading,
  themeBoardMatchesQuery,
  themeRankClass,
} from "@/lib/marketThemeBoard";

describe("marketThemeBoard formatters", () => {
  it("uses fixed gainers heading", () => {
    expect(themeBoardHeading()).toBe("主题板块涨跌");
  });

  it("formats theme board update time like fund distribution", () => {
    expect(
      formatThemeBoardUpdatedAt({
        refreshed_at: "2026-08-13T06:40:29.109425+00:00",
        trade_date: "2026-08-13",
        session_kind: "trading_day_pre_close",
      }),
    ).toBe("更新：2026-08-13 14:40");
    expect(
      formatThemeBoardUpdatedAt({
        refreshed_at: "2026-08-16T05:39:00+00:00",
        trade_date: "2026-08-14",
        session_kind: "non_trading_day",
      }),
    ).toBe("更新：2026-08-14 15:00");
    expect(formatThemeBoardUpdatedAt({ trade_date: "2026-08-14" })).toBe("更新：2026-08-14 15:00");
    expect(formatThemeBoardUpdatedAt(null)).toBeNull();
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

  it("formats board kind labels", () => {
    expect(formatBoardKindLabel("industry")).toBe("行业");
    expect(formatBoardKindLabel("concept")).toBe("概念");
    expect(formatBoardKindLabel("index")).toBe("指数");
    expect(formatBoardKindLabel(undefined)).toBe("概念");
  });

  it("maps board kind to tone class", () => {
    expect(boardKindClass("industry")).toContain("slate");
    expect(boardKindClass("index")).toContain("brand");
    expect(boardKindClass("concept")).toContain("amber");
  });

  it("formats consecutive up days", () => {
    expect(formatThemeStreak(3)).toBe("+3天");
    expect(formatThemeStreak(1)).toBe("+1天");
    expect(formatThemeStreak(-3)).toBe("-3天");
    expect(formatThemeStreak(-1)).toBe("-1天");
    expect(formatThemeStreak(0)).toBe("0天");
    expect(formatThemeStreak(null)).toBe("—");
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

    const withStreak = [
      { ...items[0], consecutive_up_days: 1 },
      { ...items[1], consecutive_up_days: 4 },
      { ...items[2], consecutive_up_days: null },
    ];
    const byStreakDesc = sortThemeBoardItems(withStreak, "streak", "desc");
    expect(byStreakDesc.map((item) => item.sector_label)).toEqual(["B", "A", "C"]);
  });

  it("matches theme boards by name or board kind", () => {
    const semiconductor = {
      sector_label: "半导体",
      board_kind: "index" as const,
    };
    expect(themeBoardMatchesQuery(semiconductor, "半导")).toBe(true);
    expect(themeBoardMatchesQuery(semiconductor, " 半 导 ")).toBe(true);
    expect(themeBoardMatchesQuery(semiconductor, "指数")).toBe(true);
    expect(themeBoardMatchesQuery(semiconductor, "红利")).toBe(false);
  });

  it("filters by search query and held-only shortcut", () => {
    const items = [
      { sector_label: "红利", board_kind: "index" as const, held_fund_count: 2, in_portfolio: true },
      { sector_label: "新能源", board_kind: "index" as const, held_fund_count: 0, in_portfolio: false },
      { sector_label: "化工", board_kind: "industry" as const, held_fund_count: 1, in_portfolio: true },
    ];
    expect(filterThemeBoardItems(items, { query: "新" }).map((item) => item.sector_label)).toEqual([
      "新能源",
    ]);
    expect(filterThemeBoardItems(items, { heldOnly: true }).map((item) => item.sector_label)).toEqual([
      "红利",
      "化工",
    ]);
    expect(
      filterThemeBoardItems(items, { query: "红", heldOnly: true }).map((item) => item.sector_label),
    ).toEqual(["红利"]);
    expect(countHeldThemeBoards(items)).toBe(2);
  });

  it("toggles sort direction on repeated column click", () => {
    expect(nextThemeSortState("change", "inflow", "desc")).toEqual({ column: "change", direction: "desc" });
    expect(nextThemeSortState("change", "change", "desc")).toEqual({ column: "change", direction: "asc" });
    expect(nextThemeSortState("change", "change", "asc")).toEqual({ column: "change", direction: "desc" });
  });
});
