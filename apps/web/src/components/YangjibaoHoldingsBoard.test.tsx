// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { YangjibaoHoldingsBoard } from "@/components/YangjibaoHoldingsBoard";
import type { Holding } from "@/lib/api";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";

vi.mock("@/lib/tradingSessionClient", () => ({
  hydrateTradingSession: () => () => undefined,
}));
vi.mock("@/lib/holdingDetailCache", () => ({
  readTradingSessionCache: () => null,
}));

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

const sectorRefresh = {
  isRefreshing: false,
  refreshError: null,
  mappingQueue: [],
  refresh: vi.fn(),
  selectMapping: vi.fn(),
  dismissMapping: vi.fn(),
  lastRefreshResult: null,
  sectorMetaByFundCode: {},
};

function makeHolding(
  fundCode: string,
  fundName: string,
  holdingAmount: number,
  dailyProfit: number,
): Holding {
  return {
    fund_code: fundCode,
    fund_name: fundName,
    holding_amount: holdingAmount,
    settled_holding_amount: holdingAmount,
    return_percent: 5,
    daily_profit: dailyProfit,
    estimated_daily_return_percent: dailyProfit / 100,
    holding_profit: holdingAmount * 0.05,
    holding_return_percent: 5,
    sector_name: fundCode === "008586" ? "人工智能与高端制造" : "红利低波",
    sector_return_percent: fundCode === "008586" ? 1.25 : -0.42,
  };
}

it("does not misrepresent a load failure as an empty portfolio", () => {
  const retry = vi.fn();
  render(
    <YangjibaoHoldingsBoard
      holdings={[]}
      sectorRefresh={sectorRefresh as never}
      loadState="error"
      loadError="持仓服务暂时不可用"
      onRetryLoad={retry}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("持仓服务暂时不可用");
  expect(screen.queryByText("上传截图，理清你的基金持仓")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("shows onboarding only for a confirmed empty portfolio", () => {
  render(
    <YangjibaoHoldingsBoard
      holdings={[]}
      sectorRefresh={sectorRefresh as never}
      loadState="ready"
    />,
  );
  expect(screen.getByText("上传截图，理清你的基金持仓")).toBeInTheDocument();
  expect(screen.getByText(OCR_PRIVACY_COPY.uploadNotice)).toBeInTheDocument();
  expect(screen.queryByText("数据仅本地识别，不上传原始截图")).not.toBeInTheDocument();
});

it("uses labeled mobile cards while retaining the sm+ desktop table", () => {
  const onSelectHolding = vi.fn();
  render(
    <YangjibaoHoldingsBoard
      holdings={[
        makeHolding(
          "008586",
          "这是一只用于验证两行展示且不会挤压收益字段的超长基金名称",
          20_000,
          120,
        ),
      ]}
      sectorRefresh={sectorRefresh as never}
      onSelectHolding={onSelectHolding}
    />,
  );

  expect(screen.getByTestId("mobile-holdings-sort")).toHaveClass("sm:hidden");
  expect(screen.getByTestId("desktop-holdings-header")).toHaveClass("hidden", "sm:grid");

  const row = screen.getByTestId("holding-row");
  expect(row).toHaveClass(
    "grid-cols-3",
    "sm:grid-cols-[minmax(0,1fr)_4.25rem_minmax(3.5rem,5rem)_4.25rem]",
    "min-h-11",
  );
  expect(within(row).getByText("估算收益")).toHaveClass("sm:hidden");
  expect(within(row).getByText("板块涨跌")).toHaveClass("sm:hidden");
  expect(within(row).getByText("持有收益")).toHaveClass("sm:hidden");
  expect(row).toHaveAccessibleName(/持有金额 20,000\.00/);
  expect(row).toHaveAccessibleName(/板块涨跌 \+1\.25%/);

  fireEvent.click(row);
  expect(onSelectHolding).toHaveBeenCalledWith({
    fund_code: "008586",
    fund_name: "这是一只用于验证两行展示且不会挤压收益字段的超长基金名称",
  });
});

it("keeps sorting available in the mobile card layout", () => {
  render(
    <YangjibaoHoldingsBoard
      holdings={[
        makeHolding("008586", "金额更高但当日收益更低", 20_000, 20),
        makeHolding("015945", "金额更低但当日收益更高", 10_000, 180),
      ]}
      sectorRefresh={sectorRefresh as never}
    />,
  );

  expect(screen.getAllByTestId("holding-row")[0]).toHaveAccessibleName(/金额更高但当日收益更低/);

  fireEvent.change(screen.getByRole("combobox", { name: "持仓排序方式" }), {
    target: { value: "daily" },
  });
  expect(screen.getAllByTestId("holding-row")[0]).toHaveAccessibleName(/金额更低但当日收益更高/);

  fireEvent.click(screen.getByRole("button", { name: "当前降序，点击切换为升序" }));
  expect(screen.getAllByTestId("holding-row")[0]).toHaveAccessibleName(/金额更高但当日收益更低/);
});

it("gives core holdings controls at least a 44px target", () => {
  render(
    <YangjibaoHoldingsBoard
      holdings={[makeHolding("008586", "示例基金", 10_000, 20)]}
      sectorRefresh={sectorRefresh as never}
      onAddHolding={vi.fn()}
      onBatchTransaction={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "隐藏金额" })).toHaveClass("h-11", "w-11");
  expect(screen.getByRole("button", { name: "刷新板块涨跌" })).toHaveClass("h-11", "w-11");
  expect(screen.getByRole("button", { name: /当前显示当日收益额/ })).toHaveClass("min-h-11");
  expect(screen.getByRole("button", { name: "当前降序，点击切换为升序" })).toHaveClass(
    "min-h-11",
    "min-w-11",
  );
  expect(screen.getByRole("button", { name: "新增持有" })).toHaveClass("min-h-11");
  expect(screen.getByRole("button", { name: "批量加减仓" })).toHaveClass("min-h-11");
  expect(screen.getByRole("button", { name: /按估算降序排列/ })).toHaveClass(
    "min-h-11",
    "min-w-11",
  );
});
