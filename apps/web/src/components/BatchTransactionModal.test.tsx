// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { BatchTransactionModal } from "@/components/BatchTransactionModal";
import { CLIPBOARD_IMAGE_PASTE_QUERY } from "@/components/ScreenshotIntakeExtras";
import { installMatchMedia, type MatchMediaController } from "@/test/matchMedia";

let matchMedia: MatchMediaController;

beforeEach(() => {
  matchMedia = installMatchMedia({ [CLIPBOARD_IMAGE_PASTE_QUERY]: true });
});

afterEach(() => {
  cleanup();
  matchMedia.restore();
  document.body.style.overflow = "";
});

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        导入交易
      </button>
      <BatchTransactionModal open={open} onClose={() => setOpen(false)} onUpload={vi.fn()} />
    </>
  );
}

it("shows transaction-record copy and the same upload entry as 同步持仓", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "导入交易" }));

  const dialog = screen.getByRole("dialog", { name: "导入交易-支持批量导入" });
  expect(dialog).toBeInTheDocument();
  expect(dialog).toHaveClass("h-full", "max-w-lg");
  expect(dialog.className).toContain("sm:h-[95vh]");
  expect(screen.getByRole("img", { name: /支付宝「交易分析」明细示意图/ })).toBeInTheDocument();
  expect(
    screen.getByText((_, node) =>
      Boolean(
        node?.tagName === "P" &&
          node.textContent === "上传「交易记录」截图，按成交写入买入/卖出，并在走势图打点",
      ),
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/交易分析/)).not.toBeInTheDocument();
  expect(screen.queryByText(/支付宝 → 基金 → 交易记录/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "上传图片" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "相册选择" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /粘贴截图/ })).not.toBeInTheDocument();
});

it("opens a moments-style image tray from 上传图片 on desktop", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "导入交易" }));
  fireEvent.click(screen.getByRole("button", { name: "上传图片" }));

  expect(screen.getByRole("dialog", { name: "上传图片" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "从本地选择图片" })).toBeInTheDocument();
  expect(screen.getByText("点击加号从电脑选择，也可直接粘贴截图")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始识别" })).toBeDisabled();
});

it("queues pasted screenshots on desktop and uploads on 开始识别", async () => {
  const onUpload = vi.fn();
  render(<BatchTransactionModal open onClose={vi.fn()} onUpload={onUpload} />);

  fireEvent.click(screen.getByRole("button", { name: "上传图片" }));

  const first = new File(["img-1"], "微信截图1.png", { type: "image/png" });
  fireEvent.paste(document, {
    clipboardData: {
      files: [],
      items: [
        {
          kind: "file",
          type: "image/png",
          getAsFile: () => first,
        },
      ],
    },
  });

  expect(onUpload).not.toHaveBeenCalled();
  await waitFor(() => {
    expect(screen.getByLabelText("待识别截图 1")).toBeInTheDocument();
  });
  expect(screen.getByRole("button", { name: "开始识别（1）" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /粘贴截图/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "开始识别（1）" }));
  expect(onUpload).toHaveBeenCalledTimes(1);
  expect(onUpload.mock.calls[0]?.[0]).toEqual([first]);
});

it("starts OCR immediately after album pick on phones", () => {
  matchMedia.setMatches(CLIPBOARD_IMAGE_PASTE_QUERY, false);
  const onUpload = vi.fn();
  render(<BatchTransactionModal open onClose={vi.fn()} onUpload={onUpload} />);

  expect(screen.getByRole("button", { name: "上传图片" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /粘贴截图/ })).not.toBeInTheDocument();

  const file = new File(["img"], "tx.png", { type: "image/png" });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  fireEvent.change(input);
  expect(onUpload).toHaveBeenCalledTimes(1);
  expect(onUpload.mock.calls[0]?.[0]).toEqual([file]);
});

it("opens the image tray when continuing from review on desktop", async () => {
  render(
    <BatchTransactionModal
      open
      continueFromReview
      onClose={vi.fn()}
      onUpload={vi.fn()}
    />,
  );
  await waitFor(() => {
    expect(screen.getByRole("dialog", { name: "上传图片" })).toBeInTheDocument();
  });
  expect(screen.getByRole("button", { name: "从本地选择图片" })).toBeInTheDocument();
  expect(screen.queryByRole("img", { name: /支付宝「交易分析」明细示意图/ })).not.toBeInTheDocument();
});
