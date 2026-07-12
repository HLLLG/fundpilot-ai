// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { LedgerBaselineModal } from "@/components/LedgerBaselineModal";
import type { Holding } from "@/lib/api";
import {
  confirmPortfolioLedgerBaseline,
  fetchPortfolioLedgerBaseline,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...original,
    fetchPortfolioLedgerBaseline: vi.fn(),
    confirmPortfolioLedgerBaseline: vi.fn(),
  };
});

const holdings: Holding[] = [
  {
    fund_code: "008586",
    fund_name: "华夏人工智能ETF联接C",
    holding_amount: 1_000,
    return_percent: 5,
  },
];

beforeEach(() => {
  vi.mocked(fetchPortfolioLedgerBaseline).mockResolvedValue({
    status: "estimated",
    ledger_version: "pl1:0:legacy",
    position_complete: false,
    cash: { balance_cny: null, status: "unknown" },
    positions: [
      {
        fund_code: "008586",
        fund_name: "华夏人工智能ETF联接C",
        settled_shares: "812.34",
        cost_basis_total_cny: "930.00",
        shares_quality: "estimated_legacy",
        cost_quality: "estimated_legacy",
      },
    ],
  });
  vi.mocked(confirmPortfolioLedgerBaseline).mockResolvedValue({
    status: "confirmed",
    ledger_version: "pl1:1:confirmed",
    position_complete: true,
    cash: { balance_cny: null, status: "unknown" },
    positions: [
      {
        fund_code: "008586",
        fund_name: "华夏人工智能ETF联接C",
        settled_shares: "810.25",
        cost_basis_total_cny: null,
        shares_quality: "user_confirmed",
      },
    ],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("keeps estimates labelled and submits only user-confirmed shares", async () => {
  const onClose = vi.fn();
  const onConfirmed = vi.fn();
  render(
    <LedgerBaselineModal
      open
      holdings={holdings}
      onClose={onClose}
      onConfirmed={onConfirmed}
    />,
  );

  const shares = await screen.findByRole("textbox", {
    name: "华夏人工智能ETF联接C实际持有份额",
  });
  expect(shares).toHaveValue("812.34");
  expect(screen.getByText(/系统当前估算 812.34 份/)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "华夏人工智能ETF联接C成本总额" })).toHaveValue("");
  expect(screen.getByText(/不会自动当作真实成本/)).toBeInTheDocument();

  fireEvent.change(shares, { target: { value: "810.25" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /我已对照原平台核对实际份额/ }));
  fireEvent.click(screen.getByRole("button", { name: "确认并冻结账本" }));

  await waitFor(() => expect(confirmPortfolioLedgerBaseline).toHaveBeenCalledOnce());
  expect(confirmPortfolioLedgerBaseline).toHaveBeenCalledWith(
    expect.objectContaining({
      cash_balance_yuan: null,
      positions: [
        {
          fund_code: "008586",
          confirmed_shares: 810.25,
          cost_basis_total_yuan: null,
        },
      ],
    }),
  );
  await waitFor(() => expect(onConfirmed).toHaveBeenCalledOnce());
  expect(onClose).toHaveBeenCalledOnce();
});
