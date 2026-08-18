// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";

import { FundTransactionList } from "@/components/FundTransactionList";
import type { DeletePortfolioTransactionResult, FundTransaction } from "@/lib/api";

function tx(overrides: Partial<FundTransaction> = {}): FundTransaction {
  return {
    id: "tx-1",
    fund_code: "021959",
    fund_name: "南方黄金股指数C",
    direction: "buy",
    amount_yuan: 1000,
    trade_time: "2026-08-17 14:55:30",
    confirm_date: "2026-08-17",
    status: "confirmed",
    shares_delta: 1000,
    nav_on_confirm: 1,
    dedup_key: "k",
    created_at: "2026-08-17T06:55:30Z",
    ...overrides,
  };
}

function deletedResult(overrides: Partial<DeletePortfolioTransactionResult> = {}): DeletePortfolioTransactionResult {
  return {
    holdings: [],
    transactions: [],
    deleted_id: "tx-1",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("打开确认框后取消不会发删除请求", () => {
  const onDeleteTransaction = vi.fn();
  render(
    <FundTransactionList
      transactions={[tx()]}
      showFundName
      onDeleteTransaction={onDeleteTransaction}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "删除买入 1,000.00 元" }));
  expect(screen.getByRole("dialog", { name: "删除这笔交易？" })).toHaveTextContent(
    "走势图买卖点会撤掉，持仓金额不变",
  );

  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog", { name: "删除这笔交易？" })).not.toBeInTheDocument();
  expect(onDeleteTransaction).not.toHaveBeenCalled();
});

it("确认删除成功后才关掉确认框并回写结果", async () => {
  const onDeleteTransaction = vi.fn().mockResolvedValue(deletedResult());
  const onDeleted = vi.fn();
  render(
    <FundTransactionList
      transactions={[tx()]}
      onDeleteTransaction={onDeleteTransaction}
      onDeleted={onDeleted}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "删除买入 1,000.00 元" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => {
    expect(onDeleted).toHaveBeenCalledTimes(1);
  });
  expect(onDeleteTransaction).toHaveBeenCalledWith("tx-1");
  expect(screen.queryByRole("dialog", { name: "删除这笔交易？" })).not.toBeInTheDocument();
});

it("删除失败时留在确认框并展示原因", async () => {
  const onDeleteTransaction = vi
    .fn()
    .mockRejectedValue(new Error("主数据库暂不可用，未写入份额/交易量表；请稍后重试"));
  const onDeleted = vi.fn();
  render(
    <FundTransactionList
      transactions={[tx()]}
      onDeleteTransaction={onDeleteTransaction}
      onDeleted={onDeleted}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "删除买入 1,000.00 元" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent("主数据库暂不可用");
  });
  expect(screen.getByRole("dialog", { name: "删除这笔交易？" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试删除" })).toBeEnabled();
  expect(onDeleted).not.toHaveBeenCalled();
});
