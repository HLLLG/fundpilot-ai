// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import type { MarketThemeBoardResponse } from "@/lib/api";

/**
 * `stale` 与 `available` 是两件事，此前这里把前者渲染成了后者的文案。
 *
 * 后端 `available = bool(items)`，只有取不到行情时才把「行情暂不可用，请稍后重试」放进
 * `message`。`stale` 只表示这份快照可能不是最新——典型触发是 api 容器刚重启
 * （`snapshot_refreshed_before_process_boot`），此时榜单**仍有完整数据**。
 * 2026-08-13 14:20 用户就撞上了这一幕：标题下写着「行情暂不可用」，下面 77 个板块的
 * 涨跌与资金流照常渲染——那次 stale 是 14:22 完成的一次部署导致的。
 */
function board(overrides: Partial<MarketThemeBoardResponse> = {}): MarketThemeBoardResponse {
  return {
    trade_date: "2026-08-13",
    session_kind: "trading_day_pre_close",
    available: true,
    from_cache: true,
    stale: false,
    refreshed_at: "2026-08-13T06:40:29.109425+00:00",
    message: null,
    sort: "change",
    items: [
      {
        rank: 1,
        sector_label: "AI医疗",
        board_kind: "concept",
        change_1d_percent: 1.76,
        change_5d_percent: 10.75,
        main_force_net_yi: 6.53,
        in_portfolio: false,
        held_fund_count: 0,
      },
    ],
    ...overrides,
  } as unknown as MarketThemeBoardResponse;
}

function renderBoard(data: MarketThemeBoardResponse | null) {
  return render(
    <ThemeSectorOverview
      data={data}
      loading={false}
      revalidating={false}
      onRefresh={vi.fn()}
    />,
  );
}

afterEach(cleanup);

describe("ThemeSectorOverview 新鲜度文案", () => {
  it("快照可能不新时不得说成行情不可用", () => {
    renderBoard(board({ stale: true }));

    expect(screen.getByText(/可能不是最新，正在刷新/)).toBeInTheDocument();
    expect(screen.queryByText(/行情暂不可用/)).not.toBeInTheDocument();
    // 数据仍在渲染，这正是不能说"不可用"的原因（桌面与移动两套布局都会出现该标签）。
    expect(screen.getAllByText("AI医疗").length).toBeGreaterThan(0);
  });

  it("真的取不到行情时才显示后端给的不可用提示", () => {
    renderBoard(
      board({
        available: false,
        items: [],
        message: "行情暂不可用，请稍后重试",
      }),
    );

    // 按设计出现两处：标题下的提示，以及空列表位置的占位。
    expect(screen.getAllByText("行情暂不可用，请稍后重试")).toHaveLength(2);
    // 这句只该由 message 产生，不该由 stale 产生。
    expect(screen.queryByText(/可能不是最新/)).not.toBeInTheDocument();
  });

  it("快照新鲜时两句话都不出现", () => {
    renderBoard(board());

    expect(screen.queryByText(/可能不是最新/)).not.toBeInTheDocument();
    expect(screen.queryByText(/行情暂不可用/)).not.toBeInTheDocument();
  });
});
