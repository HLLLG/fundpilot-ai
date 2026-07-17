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
      confirmed_shares: 12.5,
      fee_yuan: null,
      confirm_date: null,
      in_progress: false,
    }),
  );

  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(onSubmit).toHaveBeenCalledTimes(2);
});

it("requires redemption review and records platform-confirmed sell shares", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();

  render(
    <SingleFundTransactionModal
      open
      holding={holding}
      direction="sell"
      maxShares={500}
      latestNav={2}
      navDateLabel="07-17"
      reviewTargetAmountYuan={300}
      tradeability={{
        redemption_state: "open",
        redemption_fee_tiers: [
          { condition: "持有少于 7 天", fee_percent: 1.5 },
        ],
      }}
      requireRedemptionReview
      onClose={onClose}
      onSubmit={onSubmit}
    />,
  );

  expect(screen.getByRole("dialog", { name: "核对并记录减仓" })).toBeInTheDocument();
  expect(screen.getByText("¥300")).toBeInTheDocument();
  expect(screen.getByText("持有少于 7 天 · 1.5%")).toBeInTheDocument();

  fireEvent.change(screen.getByRole("textbox", { name: "同步卖出份额" }), {
    target: { value: "100" },
  });
  fireEvent.click(screen.getByRole("button", { name: /记录实际减仓/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("请先确认已在支付宝核对赎回条件与适用费率");
  expect(onSubmit).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("checkbox", { name: /我已在支付宝确认可赎回/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "实际赎回费" }), {
    target: { value: "1.5" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "原平台成交时间" }), {
    target: { value: "before_close" },
  });
  fireEvent.click(screen.getByRole("button", { name: /记录实际减仓/ }));

  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      direction: "sell",
      fund_code: "008586",
      amount_yuan: 200,
      confirmed_shares: 100,
      fee_yuan: 1.5,
      trade_time: expect.stringMatching(/ 14:30:00$/),
      confirm_date: null,
      in_progress: false,
    }),
  );
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

it("requires deletion instead of saving a zero holding amount", async () => {
  const onSubmit = vi.fn();

  render(
    <HoldingModifyModal
      open
      holding={holding}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />,
  );

  fireEvent.change(screen.getByRole("textbox", { name: "持有金额" }), {
    target: { value: "0" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "持有金额必须大于 0；如已清仓，请使用删除该基金",
  );
  expect(onSubmit).not.toHaveBeenCalled();
});
