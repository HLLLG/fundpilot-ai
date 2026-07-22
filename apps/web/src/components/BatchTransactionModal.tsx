"use client";

import { useRef } from "react";
import { ArrowDownLeft, ArrowUpRight, ChevronLeft, ChevronRight } from "lucide-react";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";
import { useDialogA11y } from "@/lib/useDialogA11y";

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
            <span className="font-bold text-[var(--brand-strong)]">「交易记录」</span>
            截图即可加减仓、同步买卖点
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

/** 交易记录示意图：占位手机框，模拟支付宝交易记录列表（加仓红/减仓绿）。 */
function TransactionRecordGuide() {
  return (
    <div className="relative mx-auto w-[62%] min-w-[200px] max-w-[250px]">
      <div className="rounded-[2.25rem] border-[7px] border-slate-900 bg-slate-900 p-[5px] shadow-[0_24px_48px_rgba(15,23,42,0.18)]">
        <div className="relative overflow-hidden rounded-[1.65rem] bg-white">
          <div className="pointer-events-none absolute left-1/2 top-0 z-10 h-[22px] w-[34%] -translate-x-1/2 rounded-b-[14px] bg-slate-900" />
          <div className="flex aspect-[390/844] w-full flex-col gap-2 bg-[#f5f7fa] px-3 pb-3 pt-8">
            <div className="mb-1 text-center text-[11px] font-bold text-slate-700">交易记录</div>
            <GuideRow direction="buy" name="易方达蓝筹精选" amount="+1,500.00" />
            <GuideRow direction="sell" name="华夏中证500ETF" amount="-800.00" />
            <GuideRow direction="buy" name="招商中证白酒" amount="+2,000.00" />
            <GuideRow direction="sell" name="广发科技先锋" amount="-1,200.00" />
            <GuideRow direction="buy" name="兴全合润" amount="+500.00" />
          </div>
        </div>
      </div>
      <div
        className="pointer-events-none absolute -bottom-3 left-1/2 h-3 w-[70%] -translate-x-1/2 rounded-[100%] bg-slate-900/10 blur-md"
        aria-hidden
      />
    </div>
  );
}

function GuideRow({
  direction,
  name,
  amount,
}: {
  direction: "buy" | "sell";
  name: string;
  amount: string;
}) {
  const isBuy = direction === "buy";
  return (
    <div className="flex items-center gap-1.5 rounded-lg bg-white px-2 py-1.5 shadow-[0_1px_4px_rgba(15,23,42,0.05)]">
      <span
        className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-white ${
          isBuy ? "bg-rose-500" : "bg-emerald-500"
        }`}
      >
        {isBuy ? <ArrowUpRight size={10} strokeWidth={2.5} /> : <ArrowDownLeft size={10} strokeWidth={2.5} />}
      </span>
      <span className="min-w-0 flex-1 truncate text-[8px] font-medium text-slate-700">{name}</span>
      <span
        className={`shrink-0 text-[8px] font-bold tabular-nums ${
          isBuy ? "profit-up" : "profit-down"
        }`}
      >
        {amount}
      </span>
    </div>
  );
}
