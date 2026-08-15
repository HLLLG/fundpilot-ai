// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { BatchTransactionConfirmModal } from "@/components/BatchTransactionConfirmModal";
import { searchFunds } from "@/lib/api";
import type { ParsedTransaction } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getFundTransactions: vi.fn(async () => ({ transactions: [] })),
  searchFunds: vi.fn(async () => []),
}));

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
  vi.mocked(searchFunds).mockResolvedValue([]);
});

function sampleTx(): ParsedTransaction {
  return {
    direction: "buy",
    fund_name: "招商医疗保健股票A",
    fund_code: "000979",
    amount_yuan: 2000,
    trade_time: "2026-08-13 14:55:30",
    confirm_date: "2026-08-13",
    in_progress: false,
  };
}

it("asks for a sync plan before writing, including markers-only", () => {
  const onConfirm = vi.fn();
  render(
    <BatchTransactionConfirmModal
      transactions={[sampleTx()]}
      heldFunds={[{ fund_code: "000979", fund_name: "招商医疗保健股票A" }]}
      onChange={vi.fn()}
      onConfirm={onConfirm}
      onContinueUpload={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "完成（1）" }));
  expect(screen.getByRole("dialog", { name: "请选择同步方案" })).toBeInTheDocument();
  expect(
    screen.getByRole("radio", { name: /同步买卖点且进行加减仓操作/ }),
  ).toHaveAttribute("aria-checked", "true");

  fireEvent.click(screen.getByRole("radio", { name: /仅同步买卖点，不进行加减仓/ }));
  fireEvent.click(screen.getByRole("button", { name: "确定" }));
  expect(onConfirm).toHaveBeenCalledWith("markers_only");
});

it("auto-fills the closest fund and asks the user to confirm", () => {
  render(
    <BatchTransactionConfirmModal
      transactions={[
        {
          ...sampleTx(),
          fund_code: "011373",
          match_source: "similar",
        },
      ]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onContinueUpload={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByText("请确认基金")).toBeInTheDocument();
  expect(screen.queryByText("请选择基金")).not.toBeInTheDocument();
  expect(screen.getByText("招商医疗保健股票A")).toBeInTheDocument();
  expect(screen.getByText("2,000.00 元")).toBeInTheDocument();
});

it("fills the closest search hit when OCR left the fund unmatched", async () => {
  vi.mocked(searchFunds).mockResolvedValue([
    { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
    { fund_code: "011374", fund_name: "招商前沿医疗保健股票C" },
  ]);
  const onChange = vi.fn();
  render(
    <BatchTransactionConfirmModal
      transactions={[{ ...sampleTx(), fund_code: null }]}
      onChange={onChange}
      onConfirm={vi.fn()}
      onContinueUpload={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  await waitFor(() => {
    expect(onChange).toHaveBeenCalled();
  });
  expect(onChange.mock.calls[0]?.[0]?.[0]).toMatchObject({
    fund_code: "011373",
    match_source: "similar",
  });
});

it("reviews transactions in a one-line Yangjibao card and hides helper copy", () => {
  render(
    <BatchTransactionConfirmModal
      transactions={[sampleTx()]}
      heldFunds={[{ fund_code: "000979", fund_name: "招商医疗保健股票A" }]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onContinueUpload={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByRole("dialog", { name: "确认识别结果" })).toBeInTheDocument();
  expect(screen.queryByText(/请核对基金、方向、金额和成交时间后再写入/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "加仓" })).toBeInTheDocument();
  expect(screen.getByText("招商医疗保健股票A")).toBeInTheDocument();
  expect(screen.getByText("2,000.00 元")).toBeInTheDocument();
  expect(screen.getByText("2026-08-13 14:55:30")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: /交易金额/ })).not.toBeInTheDocument();
  expect(screen.queryByText("金额（元）")).not.toBeInTheDocument();
  expect(screen.queryByText("实际手续费（元）")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "修改交易：招商医疗保健股票A" }));
  expect(screen.getByRole("button", { name: /基金代码：招商医疗保健股票A/ })).toHaveTextContent("000979");
  expect(screen.getByRole("textbox", { name: /基金名称：招商医疗保健股票A/ })).toHaveValue(
    "招商医疗保健股票A",
  );
  expect(screen.getByRole("textbox", { name: /交易金额：招商医疗保健股票A/ })).toHaveValue("2000");
  expect(screen.getByRole("textbox", { name: /成交时间：招商医疗保健股票A/ })).toHaveValue(
    "2026-08-13 14:55:30",
  );
  expect(screen.queryByText("手续费")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "选择基金" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
});

it("sends extra screenshots from the album without leaving the review list", () => {
  const onUploadMore = vi.fn();
  const onContinueUpload = vi.fn();
  render(
    <BatchTransactionConfirmModal
      transactions={[sampleTx()]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onContinueUpload={onContinueUpload}
      onUploadMore={onUploadMore}
      onClose={vi.fn()}
    />,
  );

  const file = new File(["img"], "more.png", { type: "image/png" });
  const input = screen.getByLabelText("继续选择交易记录截图") as HTMLInputElement;
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  fireEvent.change(input);
  expect(onUploadMore).toHaveBeenCalledWith([file]);
  expect(onContinueUpload).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "确认识别结果" })).toBeInTheDocument();
});

