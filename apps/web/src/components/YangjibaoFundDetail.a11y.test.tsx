// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import type { Holding } from "@/lib/api";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: { id: 11 } }),
}));

vi.mock("@/lib/tradingSessionClient", () => ({
  hydrateTradingSession: vi.fn(() => () => undefined),
}));

vi.mock("@/lib/holdingDetailCache", () => ({
  readHoldingDetailCache: vi.fn(() => null),
  readIntradayCache: vi.fn(() => null),
  writeHoldingDetailCache: vi.fn(),
  writeIntradayCache: vi.fn(),
}));

vi.mock("@/components/WheelDatePicker", () => ({
  resolveInitialPurchaseDate: vi.fn(() => "2025-01-01"),
  todayIsoDate: vi.fn(() => "2026-07-11"),
  WheelDatePicker: ({ value }: { value: string }) => (
    <div aria-label="日期滚轮">{value}</div>
  ),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchHoldingDetail: vi.fn(async (payload) => ({
      index: payload.index,
      holding: payload.holdings[payload.index],
      fund_code_resolved: true,
      holding_days: 120,
      first_purchase_date: "2025-01-01",
      provenance: { holding_days: "user" },
    })),
    fetchSectorIntraday: vi.fn(async () => ({
      source_type: "concept",
      source_name: "测试板块",
      points: [],
      close_change_percent: null,
    })),
    fetchFundHoldingsDistribution: vi.fn(async () => ({
      fund_code: "008586",
      status: "unavailable",
      freshness: "unknown",
      display_weight_basis: "fund_nav",
      holdings: [],
      source: "test",
      data_note: "暂无",
      generated_at: "2026-07-21T12:00:00+08:00",
      reason_codes: [],
    })),
    updateFundProfile: vi.fn(),
    updateFundProfilePurchaseDate: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  document.body.style.overflow = "";
});

const holding: Holding = {
  fund_code: "008586",
  fund_name: "测试基金",
  holding_amount: 1000,
  return_percent: 1,
};

function DetailHarness() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        查看详情
      </button>
      {open ? (
        <YangjibaoFundDetail
          holding={holding}
          holdingIndex={0}
          holdings={[holding]}
          onClose={() => setOpen(false)}
          onNavigate={vi.fn()}
          onDeleteHolding={vi.fn()}
          onAdjustHolding={vi.fn(async () => ({ holdings: [holding] }))}
          onApplyTransaction={vi.fn(async () => ({ holdings: [holding] }))}
        />
      ) : null}
    </>
  );
}

describe("YangjibaoFundDetail dialog accessibility", () => {
  it("keeps Escape and focus scoped to the topmost delete confirmation", async () => {
    render(<DetailHarness />);
    const openTrigger = screen.getByRole("button", { name: "查看详情" });
    openTrigger.focus();
    fireEvent.click(openTrigger);

    const detailDialog = await screen.findByRole("dialog", { name: "测试基金" });
    expect(within(detailDialog).getByRole("button", { name: "返回" })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
    expect(within(detailDialog).getByRole("button", { name: "展开持仓明细" })).toHaveClass(
      "min-h-11",
    );
    expect(within(detailDialog).getByRole("button", { name: "刷新分时" })).toHaveClass(
      "h-11",
      "w-11",
    );

    const deleteTrigger = within(detailDialog).getByRole("button", { name: "删除该基金" });
    deleteTrigger.focus();
    fireEvent.click(deleteTrigger);

    const deleteDialog = screen.getByRole("dialog", { name: "删除该基金？" });
    const cancelButton = within(deleteDialog).getByRole("button", { name: "取消" });
    const confirmButton = within(deleteDialog).getByRole("button", { name: "确认删除" });
    expect(cancelButton).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(confirmButton).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(cancelButton).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "删除该基金？" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "测试基金" })).toBeInTheDocument();
    expect(deleteTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "测试基金" })).not.toBeInTheDocument();
    expect(openTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });

  it("hands focus across modify, transaction, and purchase-date dialogs", async () => {
    render(<DetailHarness />);
    const openTrigger = screen.getByRole("button", { name: "查看详情" });
    openTrigger.focus();
    fireEvent.click(openTrigger);
    const detailDialog = await screen.findByRole("dialog", { name: "测试基金" });
    const modifyTrigger = within(detailDialog).getByRole("button", { name: "修改持仓" });

    modifyTrigger.focus();
    fireEvent.click(modifyTrigger);
    let childDialog = screen.getByRole("dialog", { name: "支付宝-修改持仓" });
    expect(within(childDialog).getByRole("button", { name: "返回" })).toHaveFocus();

    fireEvent.click(within(childDialog).getByRole("button", { name: "同步加仓" }));
    childDialog = screen.getByRole("dialog", { name: "支付宝-同步加仓" });
    expect(within(childDialog).getByRole("button", { name: "返回" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "支付宝-同步加仓" })).not.toBeInTheDocument();
    expect(modifyTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    modifyTrigger.focus();
    fireEvent.click(modifyTrigger);
    childDialog = screen.getByRole("dialog", { name: "支付宝-修改持仓" });
    fireEvent.click(within(childDialog).getByRole("button", { name: /^持有天数/ }));
    childDialog = await screen.findByRole("dialog", { name: "选择首次购入日期" });
    expect(within(childDialog).getByRole("button", { name: "关闭" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "选择首次购入日期" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "测试基金" })).toBeInTheDocument();
    expect(modifyTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(openTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });
});
