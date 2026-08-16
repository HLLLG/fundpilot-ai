// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeSectorOverview } from "@/components/ThemeSectorOverview";
import type { MarketThemeBoardResponse } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  // 展开资金详情会懒加载历史资金流；测试只关心同步渲染的口径行，挂起即可。
  fetchBoardFlowHistory: vi.fn(() => new Promise(() => {})),
}));

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
      {
        rank: 2,
        sector_label: "红利",
        board_kind: "index",
        change_1d_percent: 0.26,
        change_5d_percent: -0.52,
        main_force_net_yi: 9.19,
        in_portfolio: true,
        held_fund_count: 2,
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

  it("标题右侧展示更新时间", () => {
    renderBoard(board());

    expect(screen.getByText("更新：2026-08-13 14:40")).toBeInTheDocument();
  });

  it("休市时展示上一交易日收盘时间", () => {
    renderBoard(
      board({
        trade_date: "2026-08-14",
        session_kind: "non_trading_day",
        refreshed_at: "2026-08-16T05:39:00+00:00",
      }),
    );

    expect(screen.getByText("更新：2026-08-14 15:00")).toBeInTheDocument();
  });
});

describe("ThemeSectorOverview 搜索与持仓快捷入口", () => {
  it("按名称过滤板块", () => {
    renderBoard(board());

    fireEvent.change(screen.getByLabelText("按名称搜索主题板块"), {
      target: { value: "红利" },
    });

    expect(screen.getAllByText("红利").length).toBeGreaterThan(0);
    expect(screen.queryByText("AI医疗")).not.toBeInTheDocument();
    expect(screen.getByText("找到 1 个板块")).toBeInTheDocument();
  });

  it("一键只看已持仓板块", () => {
    renderBoard(board());

    fireEvent.click(screen.getByRole("button", { name: "持仓 1" }));

    expect(screen.getByRole("button", { name: "持仓 1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getAllByText("红利").length).toBeGreaterThan(0);
    expect(screen.queryByText("AI医疗")).not.toBeInTheDocument();
    expect(screen.getByText("正在查看 1 个持仓板块")).toBeInTheDocument();
  });

  it("搜索无结果时给出空状态", () => {
    renderBoard(board());

    fireEvent.change(screen.getByLabelText("按名称搜索主题板块"), {
      target: { value: "没有这个板" },
    });

    expect(screen.getByTestId("theme-sector-empty-filter")).toHaveTextContent(
      "没有匹配的板块",
    );
  });
});

/**
 * 指数主题的榜面涨幅（跟踪指数口径）与资金流（东财板块口径）不是同一个成分篮子。
 * 展开资金详情时必须补一行板块口径自身涨跌，并把旧的「可能来自不同口径」免责声明
 * 换成明确的口径说明——用户对照资金数据时才不会拿指数涨幅去解读板块资金。
 */
describe("ThemeSectorOverview 资金口径说明", () => {
  const tiers = {
    super_large_net_yi: -2.0,
    large_net_yi: -1.2,
    medium_net_yi: 1.4,
    small_net_yi: 1.8,
  };

  it("指数主题展开后显示板块口径当日涨跌与口径说明", () => {
    renderBoard(
      board({
        items: [
          {
            rank: 1,
            sector_label: "医疗",
            board_kind: "index",
            change_1d_percent: 2.1,
            change_5d_percent: 4.0,
            main_force_net_yi: -3.2,
            flow_tiers: tiers,
            source_code: "399989",
            flow_source_code: "BK0727",
            flow_change_1d_percent: -0.8,
            in_portfolio: false,
            held_fund_count: 0,
          },
        ],
      } as unknown as Partial<MarketThemeBoardResponse>),
    );

    fireEvent.click(screen.getByRole("button", { name: "展开医疗资金详情" }));

    expect(screen.getByText(/资金口径板块今日（东财 BK0727）/)).toBeInTheDocument();
    expect(screen.getByText("-0.80%")).toBeInTheDocument();
    expect(screen.getByText(/榜面涨幅为跟踪指数口径/)).toBeInTheDocument();
    expect(screen.queryByText(/可能来自不同口径/)).not.toBeInTheDocument();
  });

  it("同源主题（涨幅与资金同一 BK 板块）不重复展示口径行", () => {
    renderBoard(
      board({
        items: [
          {
            rank: 1,
            sector_label: "CPO",
            board_kind: "concept",
            change_1d_percent: 3.18,
            change_5d_percent: 4.55,
            main_force_net_yi: 114.9,
            flow_tiers: tiers,
            source_code: "BK1128",
            flow_source_code: "BK1128",
            flow_change_1d_percent: 3.18,
            in_portfolio: false,
            held_fund_count: 0,
          },
        ],
      } as unknown as Partial<MarketThemeBoardResponse>),
    );

    fireEvent.click(screen.getByRole("button", { name: "展开CPO资金详情" }));

    expect(screen.queryByText(/资金口径板块今日/)).not.toBeInTheDocument();
    expect(screen.getByText(/涨幅与资金均为东财板块口径（同源）/)).toBeInTheDocument();
  });
});
