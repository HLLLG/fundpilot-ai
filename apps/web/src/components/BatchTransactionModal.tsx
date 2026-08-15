"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Images } from "lucide-react";
import { collectImageFiles, ocrProgressLabel } from "@/lib/ocrBatchUpload";
import { useDialogA11y } from "@/lib/useDialogA11y";
import { useScreenshotIntake } from "@/lib/useScreenshotIntake";
import {
  CLIPBOARD_IMAGE_PASTE_QUERY,
  ScreenshotComposerGrid,
  ScreenshotDropOverlay,
  ScreenshotPhoneGuide,
} from "@/components/ScreenshotIntakeExtras";
import { useMediaQuery } from "@/lib/useMediaQuery";

const TRANSACTION_GUIDE_IMAGE = "/guides/alipay-transaction-records.png";

type BatchTransactionModalProps = {
  open: boolean;
  onClose: () => void;
  onUpload: (files: File[]) => void;
  isUploading?: boolean;
  uploadProgress?: { current: number; total: number } | null;
  errorMessage?: string | null;
  continueFromReview?: boolean;
};

export function BatchTransactionModal({
  open,
  onClose,
  onUpload,
  isUploading = false,
  uploadProgress = null,
  errorMessage = null,
  continueFromReview = false,
}: BatchTransactionModalProps) {
  const [mode, setMode] = useState<"chooser" | "composer">("chooser");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const isDesktopCompose = useMediaQuery(CLIPBOARD_IMAGE_PASTE_QUERY);
  const requestClose = () => {
    if (!isUploading) {
      onClose();
    }
  };
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open,
    onClose: requestClose,
    initialFocusRef: closeButtonRef,
  });
  const screenshotIntake = useScreenshotIntake({
    open,
    accepting: open && !isUploading && isDesktopCompose,
  });

  useEffect(() => {
    if (!open) {
      setMode("chooser");
      return;
    }
    if (continueFromReview && isDesktopCompose) {
      setMode("composer");
    }
  }, [open, continueFromReview, isDesktopCompose]);

  useEffect(() => {
    if (open && isDesktopCompose && screenshotIntake.items.length > 0 && mode === "chooser") {
      setMode("composer");
    }
  }, [open, isDesktopCompose, screenshotIntake.items.length, mode]);

  if (!open) {
    return null;
  }

  const dialogTitle = mode === "composer" ? "上传图片" : "导入交易-支持批量导入";
  const backAriaLabel = mode === "composer" && !continueFromReview ? "返回" : "关闭";

  const handleBack = () => {
    if (mode === "composer" && !continueFromReview) {
      screenshotIntake.clearItems();
      setMode("chooser");
      return;
    }
    onClose();
  };

  const handleSelectedFiles = (files: File[]) => {
    if (!files.length) {
      return;
    }
    if (isDesktopCompose || mode === "composer") {
      screenshotIntake.appendFiles(files);
      setMode("composer");
      return;
    }
    onUpload(files);
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-stretch justify-center overflow-hidden bg-[var(--panel)] sm:items-center sm:bg-slate-950/40 sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          requestClose();
        }
      }}
      {...screenshotIntake.dropHandlers}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="workflow-dialog relative flex h-full min-h-0 w-full max-w-lg flex-col overflow-hidden bg-[var(--panel)] sm:h-[95vh] sm:rounded-[18px] sm:shadow-[var(--shadow-lg)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="batch-transaction-modal-title"
      >
        <ScreenshotDropOverlay active={screenshotIntake.dragActive} />
        <header className="relative flex items-center justify-center border-b border-slate-200/70 bg-white px-4 pb-3.5 pt-[max(0.875rem,env(safe-area-inset-top,0px))]">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={handleBack}
            disabled={isUploading}
            className="touch-target absolute left-2 inline-flex items-center justify-center rounded-full text-slate-600 transition hover:bg-slate-100 disabled:opacity-50"
            aria-label={backAriaLabel}
          >
            <ChevronLeft size={22} strokeWidth={2.25} />
          </button>
          <h2 id="batch-transaction-modal-title" className="text-base font-bold text-slate-900">
            {dialogTitle}
          </h2>
        </header>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="sr-only"
          tabIndex={-1}
          disabled={isUploading}
          aria-label="选择一张或多张交易记录截图"
          onChange={(event) => {
            const { files } = collectImageFiles(event.target.files);
            handleSelectedFiles(files);
            event.currentTarget.value = "";
          }}
        />

        {mode === "chooser" ? (
          <>
            <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden px-5 py-2">
              <ScreenshotPhoneGuide
                src={TRANSACTION_GUIDE_IMAGE}
                alt="支付宝「交易分析」明细示意图：每条包含买入或卖出、基金名称、成交金额与成交时间"
              />
            </div>
            <p className="relative shrink-0 bg-[var(--panel)] px-5 py-2 text-center text-[15px] leading-6 text-slate-800">
              上传
              <span className="font-bold text-[var(--brand)]">「交易记录」</span>
              截图，按成交写入买入/卖出，并在走势图打点
            </p>
            {errorMessage || screenshotIntake.pasteError ? (
              <p role="alert" className="mx-5 mb-2 shrink-0 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-sm leading-5 text-[var(--danger-fg)]">
                {errorMessage ?? screenshotIntake.pasteError}
              </p>
            ) : null}

            <div className="flex shrink-0 flex-col items-center gap-1.5 bg-[var(--panel)] px-5 pt-1 pb-[max(1.25rem,calc(0.75rem+env(safe-area-inset-bottom,0px)))] sm:pb-5">
              <button
                type="button"
                disabled={isUploading}
                onClick={() => {
                  if (isDesktopCompose) {
                    setMode("composer");
                    return;
                  }
                  fileInputRef.current?.click();
                }}
                className="btn-primary w-[200px] min-h-9 py-2 text-[14px]"
              >
                <Images size={15} strokeWidth={2.25} />
                {ocrProgressLabel(isUploading, uploadProgress, "上传图片")}
              </button>
            </div>
          </>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <ScreenshotComposerGrid
                items={screenshotIntake.items}
                disabled={isUploading}
                onAdd={() => fileInputRef.current?.click()}
                onRemove={screenshotIntake.removeItem}
              />
              {errorMessage || screenshotIntake.pasteError ? (
                <p role="alert" className="mt-3 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-sm leading-5 text-[var(--danger-fg)]">
                  {errorMessage ?? screenshotIntake.pasteError}
                </p>
              ) : null}
            </div>
            <div className="flex shrink-0 flex-col items-center bg-[var(--panel)] px-5 pt-1 pb-[max(1.25rem,calc(0.75rem+env(safe-area-inset-bottom,0px)))] sm:pb-5">
              <button
                type="button"
                disabled={isUploading || screenshotIntake.items.length === 0}
                onClick={() => onUpload(screenshotIntake.files)}
                className="btn-primary w-[200px] min-h-9 py-2 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {ocrProgressLabel(
                  isUploading,
                  uploadProgress,
                  screenshotIntake.items.length
                    ? `开始识别（${screenshotIntake.items.length}）`
                    : "开始识别",
                )}
                {!isUploading ? <ChevronRight size={16} strokeWidth={2.5} /> : null}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
