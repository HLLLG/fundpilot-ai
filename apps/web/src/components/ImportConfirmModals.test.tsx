// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { AlipayOcrConfirmModal } from "@/components/AlipayOcrConfirmModal";
import { BatchTransactionConfirmModal } from "@/components/BatchTransactionConfirmModal";
import { searchFunds } from "@/lib/api";
import type { Holding, ParsedTransaction } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    searchFunds: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  document.body.style.overflow = "";
});

const holding: Holding = {
  fund_code: "110022",
  fund_name: "示例基金",
  holding_amount: 1000,
  holding_profit: 25,
  return_percent: 2.5,
  holding_return_percent: 2.5,
};

const transaction: ParsedTransaction = {
  fund_code: "110022",
  fund_name: "示例基金",
  direction: "buy",
  amount_yuan: 500,
  trade_time: "2026-07-11",
  confirm_date: null,
  in_progress: false,
};

describe("import confirmation dialogs", () => {
  it("keeps every OCR fund-search control named and at least 44px tall", async () => {
    vi.mocked(searchFunds).mockResolvedValue([
      { fund_code: "110023", fund_name: "示例搜索基金" },
    ]);

    render(
      <AlipayOcrConfirmModal
        holdings={[{ ...holding, fund_code: "000000" }]}
        onChange={vi.fn()}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByRole("textbox", { name: "搜索基金" })).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "取消基金搜索" })).toHaveClass("min-h-11");
    expect(
      await screen.findByRole("button", { name: "选择 示例搜索基金（110023）" }),
    ).toHaveClass("min-h-11");

    fireEvent.keyDown(screen.getByRole("textbox", { name: "搜索基金" }), { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "确认识别结果" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "搜索基金" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "搜索匹配" })).toHaveFocus());
  });

  it("keeps unresolved funds inside the actionable OCR review step", () => {
    const onConfirm = vi.fn();
    render(
      <AlipayOcrConfirmModal
        holdings={[{ ...holding, fund_code: "000000" }]}
        onChange={vi.fn()}
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />,
    );

    const confirm = screen.getByRole("button", { name: "请先补全基金代码（1）" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("keeps every batch-transaction fund-search control named and at least 44px tall", async () => {
    vi.mocked(searchFunds).mockResolvedValue([
      { fund_code: "110023", fund_name: "示例搜索基金" },
    ]);

    render(
      <BatchTransactionConfirmModal
        transactions={[{ ...transaction, fund_code: null }]}
        onChange={vi.fn()}
        onConfirm={vi.fn()}
        onContinueUpload={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "选择基金" }));

    expect(await screen.findByRole("textbox", { name: "搜索基金" })).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "取消基金搜索" })).toHaveClass("min-h-11");
    expect(
      await screen.findByRole("button", { name: "选择 示例搜索基金（110023）" }),
    ).toHaveClass("min-h-11");

    fireEvent.keyDown(screen.getByRole("textbox", { name: "搜索基金" }), { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "新增交易记录-支付宝" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "搜索基金" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "选择基金" })).toHaveFocus());
  });

  it("keeps an OCR persistence error inside the active dialog", () => {
    render(
      <AlipayOcrConfirmModal
        holdings={[holding]}
        errorMessage="保存失败，请重试"
        onChange={vi.fn()}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "确认识别结果" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("保存失败，请重试");
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("does not dismiss a busy transaction apply and exposes its local error", () => {
    const onClose = vi.fn();
    render(
      <BatchTransactionConfirmModal
        transactions={[transaction]}
        isBusy
        errorMessage="应用失败，请稍后重试"
        onChange={vi.fn()}
        onConfirm={vi.fn()}
        onContinueUpload={vi.fn()}
        onClose={onClose}
      />,
    );

    expect(screen.getByRole("dialog", { name: "新增交易记录-支付宝" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("应用失败，请稍后重试");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});
