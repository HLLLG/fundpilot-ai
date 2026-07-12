// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";

import { HoldingModifyModal } from "@/components/HoldingModifyModal";
import { SingleFundTransactionModal } from "@/components/SingleFundTransactionModal";
import type { Holding } from "@/lib/api";

const holding: Holding = {
  fund_code: "008586",
  fund_name: "华夏人工智能ETF联接C",
  holding_amount: 1_000,
  settled_holding_amount: 1_000,
  holding_profit: 80,
  return_percent: 8,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("keeps a single-fund transaction draft visible when the parent mutation fails", async () => {
  const onSubmit = vi
    .fn()
    .mockRejectedValueOnce(new Error("交易写入失败"))
    .mockResolvedValueOnce(undefined);
  const onClose = vi.fn();

  render(
    <SingleFundTransactionModal
      open
      holding={holding}
      direction="buy"
      latestNav={2}
      navDateLabel="07-11"
      onClose={onClose}
      onSubmit={onSubmit}
    />,
  );

  const sharesInput = screen.getByRole("textbox", { name: "同步买入份额" });
  fireEvent.change(sharesInput, { target: { value: "12.5" } });
  fireEvent.click(screen.getByRole("button", { name: "确认" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("交易写入失败");
  expect(sharesInput).toHaveValue("12.5");
  expect(screen.getByRole("dialog", { name: "支付宝-同步加仓" })).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      direction: "buy",
      fund_code: "008586",
      amount_yuan: 25,
      confirm_date: null,
      in_progress: false,
    }),
  );

  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(onSubmit).toHaveBeenCalledTimes(2);
});

it("keeps modified values after a failed save and a same-fund rerender", async () => {
  const onSubmit = vi
    .fn()
    .mockRejectedValueOnce(new Error("持仓保存失败"))
    .mockResolvedValueOnce(undefined);
  const onClose = vi.fn();
  const props = {
    open: true,
    holding,
    holdingDays: 120,
    onClose,
    onSubmit,
  };
  const { rerender } = render(<HoldingModifyModal {...props} />);

  const amountInput = screen.getByRole("textbox", { name: "持有金额" });
  const profitInput = screen.getByRole("textbox", { name: "持有收益" });
  fireEvent.change(amountInput, { target: { value: "1250.50" } });
  fireEvent.change(profitInput, { target: { value: "95.25" } });

  rerender(
    <HoldingModifyModal
      {...props}
      holding={{ ...holding, holding_amount: 9999, settled_holding_amount: 9999, holding_profit: 222 }}
    />,
  );
  expect(amountInput).toHaveValue("1250.50");
  expect(profitInput).toHaveValue("95.25");

  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("持仓保存失败");
  expect(amountInput).toHaveValue("1250.50");
  expect(profitInput).toHaveValue("95.25");
  expect(onClose).not.toHaveBeenCalled();
  expect(onSubmit).toHaveBeenCalledWith({
    settled_holding_amount: 1250.5,
    holding_profit: 95.25,
  });

  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(onSubmit).toHaveBeenCalledTimes(2);
});
