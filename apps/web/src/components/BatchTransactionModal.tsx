"use client";

import Image from "next/image";
import { useRef } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";
import { useDialogA11y } from "@/lib/useDialogA11y";

const TRANSACTION_GUIDE_IMAGE = "/guides/alipay-transaction-records.png";

type BatchTransactionModalProps = {
  open: boolean;
  onClose: () => void;
  onUpload: (file: File) => void;
  isUploading?: boolean;
  errorMessage?: string | null;
};

export function BatchTransactionModal({
  open,
  onClose,
  onUpload,
  isUploading = false,
  errorMessage = null,
}: BatchTransactionModalProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
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

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/40 sm:items-center sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          requestClose();
        }
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[94vh] w-full max-w-md flex-col overflow-hidden rounded-t-[28px] bg-[#f5f7fa] shadow-2xl sm:rounded-[28px]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="batch-transaction-modal-title"
      >
        <header className="relative flex items-center justify-center border-b border-slate-200/70 bg-white px-4 py-3.5">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            disabled={isUploading}
            className="touch-target absolute left-2 inline-flex items-center justify-center rounded-full text-slate-600 transition hover:bg-slate-100 disabled:opacity-50"
            aria-label="关闭"
          >
            <ChevronLeft size={22} strokeWidth={2.25} />
          </button>
          <h2 id="batch-transaction-modal-title" className="text-base font-bold text-slate-900">
            支付宝-批量加减仓
          </h2>
        </header>

        <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-5 pb-2 pt-6">
          <TransactionRecordGuide />
          <p className="mt-6 text-center text-[15px] leading-7 text-slate-800">
            上传
            <span className="font-bold text-[var(--brand-strong)]">「交易记录」或「交易分析」</span>
            截图即可加减仓、同步买卖点
          </p>
          <p className="mt-2 text-center text-[13px] leading-5 text-slate-500">
            路径：支付宝 → 我的 → 总资产 → 基金 → 交易记录
            <br />
            也可在基金持有页切到「交易分析」Tab 后截图
            <br />
            持仓总览截图请走「上传截图 / 新增持有」
          </p>
          <p className="mt-4 rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)]/80 px-3 py-2 text-xs leading-5 text-slate-600">
            {OCR_PRIVACY_COPY.uploadNotice}
          </p>
          {errorMessage ? (
            <p role="alert" className="mt-3 w-full rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-sm leading-5 text-[var(--danger-fg)]">
              {errorMessage}
            </p>
          ) : null}
        </div>

        <div className="space-y-3 bg-[#f5f7fa] px-5 pb-8 pt-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            tabIndex={-1}
            disabled={isUploading}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                onUpload(file);
              }
              event.currentTarget.value = "";
            }}
          />
          <button
            type="button"
            disabled={isUploading}
            onClick={() => fileInputRef.current?.click()}
            className="flex w-full items-center justify-center gap-1 rounded-full bg-gradient-to-r from-[#4a86e8] to-[#3b78e0] px-4 py-4 text-[16px] font-bold text-white shadow-[0_10px_24px_rgba(74,134,232,0.35)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isUploading ? "识别中..." : "去相册选择"}
            {!isUploading ? <ChevronRight size={18} strokeWidth={2.5} /> : null}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 交易记录示意图：真实版式截图。
 *
 * 原来是手绘的占位插图（几行「基金名 + ±金额」的胶囊），和支付宝真实的「交易记录 /
 * 交易分析」页长得完全不一样：真实页每条是「买入/卖出 + 基金名称 + 金额元 + 成交时间」，
 * 而解析器正是靠买入/卖出锚点、`元` 金额和成交时间戳定位交易的。示意图缺这三样，照着它
 * 截图的用户会传上来一张解析不出任何交易的图。这里换成按真实版式渲染并跑通识别的截图。
 */
function TransactionRecordGuide() {
  return (
    <div className="relative mx-auto w-[62%] min-w-[200px] max-w-[250px]">
      <div className="rounded-[2.25rem] border-[7px] border-slate-900 bg-slate-900 p-[5px] shadow-[0_24px_48px_rgba(15,23,42,0.18)]">
        <div className="relative overflow-hidden rounded-[1.65rem] bg-white">
          <div className="pointer-events-none absolute left-1/2 top-0 z-10 h-[22px] w-[34%] -translate-x-1/2 rounded-b-[14px] bg-slate-900" />
          <Image
            src={TRANSACTION_GUIDE_IMAGE}
            alt="支付宝「交易记录 / 交易分析」页面示意图：每条包含买入或卖出、基金名称、成交金额与成交时间"
            width={472}
            height={1021}
            className="aspect-[390/844] h-auto w-full object-cover object-top"
            draggable={false}
          />
        </div>
      </div>
      <div
        className="pointer-events-none absolute -bottom-3 left-1/2 h-3 w-[70%] -translate-x-1/2 rounded-[100%] bg-slate-900/10 blur-md"
        aria-hidden
      />
    </div>
  );
}
