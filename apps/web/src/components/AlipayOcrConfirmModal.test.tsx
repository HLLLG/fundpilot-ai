// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { AlipayOcrConfirmModal } from "@/components/AlipayOcrConfirmModal";

vi.mock("@/lib/api", () => ({
  searchFunds: vi.fn(async () => [
    { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
    { fund_code: "011374", fund_name: "招商前沿医疗保健股票C" },
  ]),
}));

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

const unmatchedHolding = {
  fund_code: "000000",
  fund_name: "招商医疗保健股票A",
  holding_amount: 1000,
  return_percent: 0,
  holding_profit: 12,
};

it("keeps the confirm dialog free of helper copy and the workflow rail", () => {
  render(
    <AlipayOcrConfirmModal
      holdings={[{ ...unmatchedHolding, fund_code: "015788" }]}
      fundCodeResolutions={[
        {
          fund_name: "招商医疗保健股票A",
          fund_code: "015788",
          source: "akshare",
          resolved: true,
        },
      ]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.queryByText(/可修改基金代码/)).not.toBeInTheDocument();
  expect(screen.queryByText("截图进入")).not.toBeInTheDocument();
  expect(screen.queryByText("校对数据")).not.toBeInTheDocument();
  expect(screen.queryByText("确认写入")).not.toBeInTheDocument();
  expect(screen.queryByText("akshare")).not.toBeInTheDocument();
});

it("does not delay-fill unmatched funds after the confirm dialog opens", async () => {
  const onChange = vi.fn();
  render(
    <AlipayOcrConfirmModal
      holdings={[unmatchedHolding]}
      fundCodeResolutions={[
        {
          fund_name: "招商医疗保健股票A",
          fund_code: null,
          source: null,
          resolved: false,
        },
      ]}
      onChange={onChange}
      onConfirm={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: /基金代码：招商医疗保健股票A/ })).toHaveTextContent(
    "待匹配",
  );
  await new Promise((resolve) => {
    window.setTimeout(resolve, 50);
  });
  expect(onChange).not.toHaveBeenCalled();
});

it("sends extra screenshots from the album without leaving the review list", () => {
  const onUploadMore = vi.fn();
  const onContinueUpload = vi.fn();
  render(
    <AlipayOcrConfirmModal
      holdings={[{ ...unmatchedHolding, fund_code: "011373" }]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onContinueUpload={onContinueUpload}
      onUploadMore={onUploadMore}
      onClose={vi.fn()}
    />,
  );

  const file = new File(["img"], "more.png", { type: "image/png" });
  const input = screen.getByLabelText("继续选择持仓截图") as HTMLInputElement;
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  fireEvent.change(input);
  expect(onUploadMore).toHaveBeenCalledWith([file]);
  expect(onContinueUpload).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "确认识别结果" })).toBeInTheDocument();
});

it("asks the user to confirm an auto-filled similar fund", () => {
  render(
    <AlipayOcrConfirmModal
      holdings={[{ ...unmatchedHolding, fund_code: "011373" }]}
      fundCodeResolutions={[
        {
          fund_name: "招商医疗保健股票A",
          fund_code: "011373",
          source: "similar",
          resolved: true,
          message: "请确认基金",
        },
      ]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByText("请确认基金")).toBeInTheDocument();
  expect(screen.queryByText(/未在东财基金库匹配到代码/)).not.toBeInTheDocument();
  expect(screen.queryByText("相近匹配")).not.toBeInTheDocument();
});

it("shows holdings as review cards and only opens editors when asked", () => {
  render(
    <AlipayOcrConfirmModal
      holdings={[{ ...unmatchedHolding, fund_code: "015788" }]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: /基金代码：招商医疗保健股票A/ })).toHaveTextContent("015788");
  expect(screen.queryByRole("button", { name: /搜索/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: /持有金额/ })).not.toBeInTheDocument();
  expect(screen.getByText("1,000.00")).toBeInTheDocument();
  expect(screen.getByText("+12.00")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /修改持仓：招商医疗保健股票A/ }));
  expect(screen.getByRole("textbox", { name: /持有金额：招商医疗保健股票A/ })).toHaveValue("1000");
  expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
});

it("opens fund search from the fund code", () => {
  render(
    <AlipayOcrConfirmModal
      holdings={[{ ...unmatchedHolding, fund_code: "015788" }]}
      onChange={vi.fn()}
      onConfirm={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /基金代码：招商医疗保健股票A/ }));
  expect(screen.getByLabelText("搜索基金")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消基金搜索" })).toBeInTheDocument();
});
