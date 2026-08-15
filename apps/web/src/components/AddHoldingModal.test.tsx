// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { AddHoldingModal } from "@/components/AddHoldingModal";
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
      <button type="button" onClick={() => setOpen(true)}>同步持仓</button>
      <AddHoldingModal
        open={open}
        onClose={() => setOpen(false)}
        onUpload={vi.fn()}
        onManualSubmit={vi.fn()}
      />
    </>
  );
}

it("opens the holdings import dialog and restores focus on Escape", () => {
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "同步持仓" });
  trigger.focus();
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "同步持仓-支持批量导入" });
  expect(dialog).toBeInTheDocument();
  expect(dialog).toHaveClass("h-full");
  expect(dialog.className).toContain("sm:h-[95vh]");
  expect(screen.queryByText("支付宝 → 我的 → 总资产 → 基金 → 我的持有")).not.toBeInTheDocument();
  expect(screen.getByRole("img", { name: "支付宝「我的持有」页面示意图" })).toHaveAttribute(
    "src",
    expect.stringContaining("alipay-holdings-overview.png"),
  );
  expect(screen.getByRole("button", { name: "上传图片" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "相册选择" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /粘贴截图/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
  expect(document.body.style.overflow).toBe("hidden");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});

it("uses associated labels and keeps an incomplete manual draft un-submittable", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "同步持仓" }));
  fireEvent.click(screen.getByRole("button", { name: "手动输入" }));

  expect(screen.getByRole("textbox", { name: "基金名称" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "持有金额" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存（0）" })).toBeDisabled();
});

it("lets the album picker select multiple screenshots", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "同步持仓" }));
  const fileInput = document.querySelector('input[type="file"]');
  expect(fileInput).toHaveAttribute("multiple");
  expect(fileInput).toHaveAttribute("accept", "image/*");
});

it("uploads a pasted WeChat screenshot without saving a local file first", async () => {
  const onUpload = vi.fn();
  render(
    <AddHoldingModal
      open
      onClose={vi.fn()}
      onUpload={onUpload}
      onManualSubmit={vi.fn()}
    />,
  );

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
  expect(screen.queryByRole("button", { name: /粘贴截图|再粘一张/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "从相册添加" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "手动输入" })).not.toBeInTheDocument();

  const second = new File(["img-2"], "微信截图2.png", { type: "image/png" });
  fireEvent.paste(document, {
    clipboardData: {
      files: [],
      items: [
        {
          kind: "file",
          type: "image/png",
          getAsFile: () => second,
        },
      ],
    },
  });

  expect(onUpload).not.toHaveBeenCalled();
  await waitFor(() => {
    expect(screen.getByLabelText("待识别截图 2")).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole("button", { name: "开始识别（2）" }));
  expect(onUpload).toHaveBeenCalledTimes(1);
  expect(onUpload.mock.calls[0]?.[0]).toEqual([first, second]);
});

it("opens a moments-style image tray from 上传图片 on desktop", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "同步持仓" }));
  fireEvent.click(screen.getByRole("button", { name: "上传图片" }));

  expect(screen.getByRole("dialog", { name: "上传图片" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "从本地选择图片" })).toBeInTheDocument();
  expect(screen.getByText("点击加号从电脑选择，也可直接粘贴截图")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始识别" })).toBeDisabled();
});

it("hides the image tray on coarse-pointer phones and keeps album import", () => {
  matchMedia.setMatches(CLIPBOARD_IMAGE_PASTE_QUERY, false);
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "同步持仓" }));
  expect(screen.getByRole("button", { name: "上传图片" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "手动输入" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /粘贴截图/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "从本地选择图片" })).not.toBeInTheDocument();
});

it("starts OCR immediately after album pick on phones", () => {
  matchMedia.setMatches(CLIPBOARD_IMAGE_PASTE_QUERY, false);
  const onUpload = vi.fn();
  render(
    <AddHoldingModal
      open
      onClose={vi.fn()}
      onUpload={onUpload}
      onManualSubmit={vi.fn()}
    />,
  );
  const file = new File(["img"], "holding.png", { type: "image/png" });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  fireEvent.change(input);
  expect(onUpload).toHaveBeenCalledTimes(1);
  expect(onUpload.mock.calls[0]?.[0]).toEqual([file]);
});

it("opens the image tray when continuing from review on desktop", async () => {
  render(
    <AddHoldingModal
      open
      continueFromReview
      onClose={vi.fn()}
      onUpload={vi.fn()}
      onManualSubmit={vi.fn()}
    />,
  );
  await waitFor(() => {
    expect(screen.getByRole("dialog", { name: "上传图片" })).toBeInTheDocument();
  });
  expect(screen.getByRole("button", { name: "从本地选择图片" })).toBeInTheDocument();
  expect(screen.queryByRole("img", { name: "支付宝「我的持有」页面示意图" })).not.toBeInTheDocument();
});
