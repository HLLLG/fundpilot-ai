// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";
import { fetchHoldingDetail, fetchSectorIntraday, type Holding } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchHoldingDetail: vi.fn(),
    fetchSectorIntraday: vi.fn(),
  };
});

vi.mock("@/lib/tradingSessionClient", () => ({
  hydrateTradingSession: () => () => undefined,
}));

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: { id: 1 } }),
}));

// 图表与披露区不参与本用例，替换成占位以免引入额外的网络/canvas 依赖。
vi.mock("@/components/PerformanceTrendPanel", () => ({
  PerformanceTrendPanel: () => <div data-testid="performance-panel" />,
}));
vi.mock("@/components/IntradayPercentChart", () => ({
  IntradayPercentChart: () => <div data-testid="intraday-chart" />,
  buildFlatIntradayPoints: () => [],
}));
vi.mock("@/components/FundHoldingsDisclosure", () => ({
  FundHoldingsDisclosure: () => <div data-testid="holdings-disclosure" />,
}));

const HOLDING: Holding = {
  fund_code: "025856",
  fund_name: "华夏中证电网设备主题ETF联接A",
  holding_amount: 7652.99,
  holding_profit: 225.7,
  holding_return_percent: 3.04,
  daily_profit: 104.71,
  daily_return_percent: 1.31,
  sector_name: "电网设备",
  sector_return_percent: 1.37,
} as Holding;

beforeEach(() => {
  vi.mocked(fetchHoldingDetail).mockRejectedValue(new Error("detail unavailable"));
  vi.mocked(fetchSectorIntraday).mockRejectedValue(new Error("intraday unavailable"));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  document.body.style.overflow = "";
});

async function openDeleteConfirm() {
  fireEvent.click(screen.getByRole("button", { name: "删除该基金" }));
  return screen.getByRole("dialog", { name: "删除该基金？" });
}

it("删除失败时保留确认框并展示服务端原因，不静默关闭", async () => {
  // 复刻线上现象：主库不可用，DELETE 返回 503。
  const onDeleteHolding = vi
    .fn()
    .mockRejectedValue(new Error("主数据库暂不可用，未写入份额/交易量表；请稍后重试"));
  const onClose = vi.fn();

  render(
    <YangjibaoFundDetail
      holding={HOLDING}
      holdingIndex={0}
      holdings={[HOLDING]}
      onClose={onClose}
      onNavigate={() => undefined}
      onDeleteHolding={onDeleteHolding}
    />,
  );

  const dialog = await openDeleteConfirm();
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  // 断言限定在确认框内：详情接口本身也可能挂着一个 role="alert"。
  await waitFor(() => {
    expect(within(dialog).getByRole("alert")).toHaveTextContent("主数据库暂不可用");
  });

  // 关键断言：确认框还在、详情页没被关掉。
  // 回归前的实现会同时关掉两层弹窗，用户只看到"行闪一下又回来"。
  expect(screen.getByRole("dialog", { name: "删除该基金？" })).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(onDeleteHolding).toHaveBeenCalledTimes(1);

  // 失败后主按钮变成可重试。
  expect(screen.getByRole("button", { name: "重试删除" })).toBeEnabled();
});

it("删除成功后才收起确认框并关闭详情页", async () => {
  const onDeleteHolding = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();

  render(
    <YangjibaoFundDetail
      holding={HOLDING}
      holdingIndex={0}
      holdings={[HOLDING]}
      onClose={onClose}
      onNavigate={() => undefined}
      onDeleteHolding={onDeleteHolding}
    />,
  );

  await openDeleteConfirm();
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  expect(screen.queryByRole("dialog", { name: "删除该基金？" })).not.toBeInTheDocument();
  expect(screen.queryByText(/删除失败/)).not.toBeInTheDocument();
});

it("删除请求在飞时禁用两个按钮，避免重复提交", async () => {
  let resolveDelete: (() => void) | undefined;
  const onDeleteHolding = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveDelete = () => resolve();
      }),
  );

  render(
    <YangjibaoFundDetail
      holding={HOLDING}
      holdingIndex={0}
      holdings={[HOLDING]}
      onClose={() => undefined}
      onNavigate={() => undefined}
      onDeleteHolding={onDeleteHolding}
    />,
  );

  await openDeleteConfirm();
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => {
    expect(screen.getByRole("button", { name: "删除中…" })).toBeDisabled();
  });
  expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();

  // 再点几次也只发一次请求。
  fireEvent.click(screen.getByRole("button", { name: "删除中…" }));
  expect(onDeleteHolding).toHaveBeenCalledTimes(1);

  resolveDelete?.();
});
