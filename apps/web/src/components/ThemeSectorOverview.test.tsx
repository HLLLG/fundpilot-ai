// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import {
  fetchBoardFlowHistory,
  type BoardFlowHistoryResponse,
  type MarketThemeBoardResponse,
} from "@/lib/api";
import { installMatchMedia, type MatchMediaController } from "@/test/matchMedia";

const DESKTOP_QUERY = "(min-width: 640px)";
let matchMedia: MatchMediaController;

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, fetchBoardFlowHistory: vi.fn() };
});

beforeEach(() => {
  matchMedia = installMatchMedia({ [DESKTOP_QUERY]: false });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  matchMedia.restore();
});

function themeData(): MarketThemeBoardResponse {
  return {
    available: true,
    sort: "change",
    trade_date: "2026-07-11",
    refreshed_at: "2026-07-11T10:30:00+08:00",
    items: [
      {
        sector_label: "半导体",
        board_kind: "industry",
        change_1d_percent: 2.15,
        change_5d_percent: -1.2,
        main_force_net_yi: 12.5,
        flow_tiers: {
          super_large_net_yi: 8,
          large_net_yi: 4.5,
          medium_net_yi: -1,
          small_net_yi: 0.3,
        },
        flow_source_code: "BK1036",
        held_fund_count: 2,
        in_portfolio: true,
        rank: 1,
      },
      {
        sector_label: "新能源",
        board_kind: "concept",
        change_1d_percent: -0.8,
        change_5d_percent: 1.5,
        main_force_net_yi: null,
        held_fund_count: 0,
        in_portfolio: false,
        rank: 2,
      },
    ],
  };
}

function flowHistory(): BoardFlowHistoryResponse {
  return {
    available: true,
    range: "week",
    sector_label: "半导体",
    board_code: "BK1036",
    cumulative_net_yi: 3.5,
    points: [
      { date: "2026-07-10", main_force_net_yi: -1.5 },
      { date: "2026-07-11", main_force_net_yi: 5 },
    ],
  };
}

describe("ThemeSectorOverview responsive presentation", () => {
  it("renders complete mobile cards without mounting the desktop table", () => {
    vi.mocked(fetchBoardFlowHistory).mockResolvedValue(flowHistory());
    const onViewDipFunds = vi.fn();
    const onAddFocusSector = vi.fn();

    render(
      <ThemeSectorOverview
        data={themeData()}
        loading={false}
        revalidating={false}
        onRefresh={vi.fn()}
        onViewDipFunds={onViewDipFunds}
        onAddFocusSector={onAddFocusSector}
        focusSectors={[]}
      />,
    );

    const mobileList = screen.getByTestId("theme-sector-mobile-list");
    const mobileCard = within(mobileList).getByTestId("theme-sector-mobile-card-半导体");
    expect(within(mobileCard).getByText("半导体")).toBeInTheDocument();
    expect(within(mobileCard).getByText("今日")).toBeInTheDocument();
    expect(within(mobileCard).getByText("5日")).toBeInTheDocument();
    expect(within(mobileCard).getByText("主力资金")).toBeInTheDocument();
    expect(within(mobileCard).getByText("持仓 2 只")).toBeInTheDocument();
    expect(within(mobileCard).getByText("+2.15%")).toBeInTheDocument();
    expect(within(mobileCard).getByText("-1.20%")).toBeInTheDocument();
    expect(within(mobileCard).getByText("+12.50亿")).toBeInTheDocument();

    fireEvent.click(within(mobileCard).getByRole("button", { name: "看大跌" }));
    fireEvent.click(within(mobileCard).getByRole("button", { name: "加关注" }));
    expect(onViewDipFunds).toHaveBeenCalledWith("半导体");
    expect(onAddFocusSector).toHaveBeenCalledWith("半导体");
    expect(screen.queryByTestId("theme-sector-desktop-table")).not.toBeInTheDocument();
  });

  it("renders a semantic sortable desktop table without mounting mobile cards", async () => {
    matchMedia.setMatches(DESKTOP_QUERY, true);
    vi.mocked(fetchBoardFlowHistory).mockResolvedValue(flowHistory());
    render(
      <ThemeSectorOverview
        data={themeData()}
        loading={false}
        revalidating={false}
        onRefresh={vi.fn()}
      />,
    );

    const desktopWrapper = screen.getByTestId("theme-sector-desktop-table");
    const table = within(desktopWrapper).getByRole("table", {
      name: /主题板块行情/,
    });
    const todayHeader = within(table).getByRole("columnheader", { name: /今日/ });
    const fiveDayHeader = within(table).getByRole("columnheader", { name: /5日/ });
    expect(todayHeader).toHaveAttribute("scope", "col");
    expect(todayHeader).toHaveAttribute("aria-sort", "descending");
    expect(fiveDayHeader).toHaveAttribute("scope", "col");
    expect(fiveDayHeader).toHaveAttribute("aria-sort", "none");

    const expandButton = within(table).getByRole("button", {
      name: "展开半导体资金详情",
    });
    expect(expandButton).toHaveAttribute("aria-expanded", "false");
    expect(expandButton).toHaveClass("h-11", "w-11");
    fireEvent.click(expandButton);

    const collapseButton = within(table).getByRole("button", {
      name: "收起半导体资金详情",
    });
    expect(collapseButton).toHaveAttribute("aria-expanded", "true");
    const detailsId = collapseButton.getAttribute("aria-controls");
    expect(detailsId).toBeTruthy();
    expect(document.getElementById(detailsId ?? "")).toBeInTheDocument();
    await waitFor(() => expect(fetchBoardFlowHistory).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("theme-sector-mobile-list")).not.toBeInTheDocument();
  });

  it("preserves expanded history when switching from mobile to desktop", async () => {
    vi.mocked(fetchBoardFlowHistory).mockResolvedValue(flowHistory());
    render(
      <ThemeSectorOverview
        data={themeData()}
        loading={false}
        revalidating={false}
        onRefresh={vi.fn()}
      />,
    );

    const mobileCard = screen.getByTestId("theme-sector-mobile-card-半导体");
    fireEvent.click(
      within(mobileCard).getByRole("button", { name: "展开半导体资金详情" }),
    );
    await waitFor(() => expect(fetchBoardFlowHistory).toHaveBeenCalledTimes(1));
    expect(await within(mobileCard).findByRole("img")).toBeInTheDocument();

    act(() => matchMedia.setMatches(DESKTOP_QUERY, true));

    expect(screen.queryByTestId("theme-sector-mobile-list")).not.toBeInTheDocument();
    const desktopTable = screen.getByTestId("theme-sector-desktop-table");
    expect(
      within(desktopTable).getByRole("button", { name: "收起半导体资金详情" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(within(desktopTable).getByRole("img")).toBeInTheDocument();
    expect(fetchBoardFlowHistory).toHaveBeenCalledTimes(1);
  });

  it("pages long mobile board lists instead of mounting every card at once", () => {
    const base = themeData();
    const data: MarketThemeBoardResponse = {
      ...base,
      items: Array.from({ length: 23 }, (_, index) => ({
        ...base.items[1],
        sector_label: `板块${index + 1}`,
        rank: index + 1,
        change_1d_percent: 23 - index,
      })),
    };
    render(
      <ThemeSectorOverview
        data={data}
        loading={false}
        revalidating={false}
        onRefresh={vi.fn()}
      />,
    );

    const mobileList = screen.getByTestId("theme-sector-mobile-list");
    expect(within(mobileList).getAllByRole("article")).toHaveLength(10);
    const more = within(mobileList).getByRole("button", {
      name: "显示更多板块（还剩 13 个）",
    });
    fireEvent.click(more);
    expect(within(mobileList).getAllByRole("article")).toHaveLength(20);
    fireEvent.click(
      within(mobileList).getByRole("button", { name: "显示更多板块（还剩 3 个）" }),
    );
    expect(within(mobileList).getAllByRole("article")).toHaveLength(23);
    const collapse = within(mobileList).getByRole("button", {
      name: "收起到前 10 个板块",
    });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(collapse);
    expect(within(mobileList).getAllByRole("article")).toHaveLength(10);
  });
});

describe("ThemeSectorOverview flow history recovery", () => {
  it("clears a failed cached response and retries on demand", async () => {
    vi.mocked(fetchBoardFlowHistory)
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(flowHistory());

    render(
      <ThemeSectorOverview
        data={themeData()}
        loading={false}
        revalidating={false}
        onRefresh={vi.fn()}
      />,
    );

    const mobileCard = screen.getByTestId("theme-sector-mobile-card-半导体");
    fireEvent.click(
      within(mobileCard).getByRole("button", { name: "展开半导体资金详情" }),
    );

    await waitFor(() => expect(fetchBoardFlowHistory).toHaveBeenCalledTimes(1));
    expect(await within(mobileCard).findByText("历史资金流加载失败")).toBeInTheDocument();

    fireEvent.click(within(mobileCard).getByRole("button", { name: "重试" }));

    await waitFor(() => expect(fetchBoardFlowHistory).toHaveBeenCalledTimes(2));
    expect(
      await within(mobileCard).findByRole("img", {
        name: "板块主力净流入历史柱状图，单位亿元",
      }),
    ).toBeInTheDocument();
  });
});
