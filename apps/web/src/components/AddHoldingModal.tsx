"use client";

import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Images,
  PenLine,
  Plus,
  X,
} from "lucide-react";
import type { Holding } from "@/lib/api";
import { collectImageFiles, ocrProgressLabel } from "@/lib/ocrBatchUpload";
import { useDialogA11y } from "@/lib/useDialogA11y";
import { useScreenshotIntake } from "@/lib/useScreenshotIntake";
import {
  ScreenshotComposerGrid,
  ScreenshotDropOverlay,
  ScreenshotPhoneGuide,
  CLIPBOARD_IMAGE_PASTE_QUERY,
} from "@/components/ScreenshotIntakeExtras";
import { useMediaQuery } from "@/lib/useMediaQuery";

const ALIPAY_GUIDE_IMAGE = "/guides/alipay-holdings-overview.png";

const ALIPAY_CHANNEL_COPY: { title: string; hint: ReactNode } = {
  title: "同步持仓-支持批量导入",
  hint: (
    <>
      上传支付宝
      <span className="font-bold text-[var(--brand)]">「我的持有」</span>
      总览截图，对齐当前持仓金额
    </>
  ),
};

type AddHoldingModalProps = {
  open: boolean;
  onClose: () => void;
  onUpload: (files: File[]) => void;
  onManualSubmit: (holdings: Holding[]) => void | Promise<void>;
  isUploading?: boolean;
  uploadProgress?: { current: number; total: number } | null;
  isSubmitting?: boolean;
  errorMessage?: string | null;
  continueFromReview?: boolean;
};

type ManualEntry = {
  id: string;
  fund_name: string;
  holding_amount: string;
  holding_profit: string;
  collapsed: boolean;
};

function createManualEntry(): ManualEntry {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    fund_name: "",
    holding_amount: "",
    holding_profit: "",
    collapsed: false,
  };
}

export function AddHoldingModal({
  open,
  onClose,
  onUpload,
  onManualSubmit,
  isUploading = false,
  uploadProgress = null,
  isSubmitting = false,
  errorMessage = null,
  continueFromReview = false,
}: AddHoldingModalProps) {
  const [mode, setMode] = useState<"chooser" | "composer" | "manual">("chooser");
  const [entries, setEntries] = useState<ManualEntry[]>([createManualEntry()]);
  const [formError, setFormError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const isDesktopCompose = useMediaQuery(CLIPBOARD_IMAGE_PASTE_QUERY);
  const busy = isUploading || isSubmitting;
  const requestClose = () => {
    if (!busy) {
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
    accepting: open && !busy && mode !== "manual" && isDesktopCompose,
  });

  useEffect(() => {
    if (!open) {
      setMode("chooser");
      setEntries([createManualEntry()]);
      setFormError(null);
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

  const validCount = entries.filter((entry) => isEntryValid(entry)).length;
  const canSubmit = validCount > 0 && !busy;

  const updateEntry = (id: string, patch: Partial<ManualEntry>) => {
    setEntries((current) =>
      current.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)),
    );
  };

  const removeEntry = (id: string) => {
    setEntries((current) => {
      if (current.length <= 1) {
        return [createManualEntry()];
      }
      return current.filter((entry) => entry.id !== id);
    });
  };

  const handleManualSubmit = async () => {
    const parsed: Holding[] = [];
    for (const entry of entries) {
      const holding = entryToHolding(entry);
      if (holding) {
        parsed.push(holding);
      }
    }

    if (!parsed.length) {
      setFormError("请至少填写一只基金的名称与持有金额。");
      return;
    }

    setFormError(null);
    await onManualSubmit(parsed);
  };

  const channelCopy = ALIPAY_CHANNEL_COPY;
  const dialogTitle =
    mode === "manual" ? "手动新增" : mode === "composer" ? "上传图片" : channelCopy.title;
  const backAriaLabel =
    mode === "manual" || (mode === "composer" && !continueFromReview) ? "返回" : "关闭";

  const handleBack = () => {
    if (mode === "manual") {
      setFormError(null);
      setMode("chooser");
      return;
    }
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
        aria-labelledby="add-holding-modal-title"
      >
        <ScreenshotDropOverlay active={screenshotIntake.dragActive} />
        <header className="relative flex items-center justify-center border-b border-slate-200/70 bg-white px-4 pb-3.5 pt-[max(0.875rem,env(safe-area-inset-top,0px))]">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={handleBack}
            disabled={busy}
            className="touch-target absolute left-2 inline-flex items-center justify-center rounded-full text-slate-600 transition hover:bg-slate-100 disabled:opacity-50"
            aria-label={backAriaLabel}
          >
            <ChevronLeft size={22} strokeWidth={2.25} />
          </button>
          <h2 id="add-holding-modal-title" className="text-base font-bold text-slate-900">
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
          disabled={busy}
          aria-label="选择一张或多张持仓截图"
          onChange={(event) => {
            const { files } = collectImageFiles(event.target.files);
            handleSelectedFiles(files);
            event.currentTarget.value = "";
          }}
        />

        {mode === "chooser" ? (
          <>
            <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden px-5 py-2">
              <ScreenshotPhoneGuide src={ALIPAY_GUIDE_IMAGE} alt="支付宝「我的持有」页面示意图" />
            </div>
            <p className="relative shrink-0 bg-[var(--panel)] px-5 py-2 text-center text-[15px] leading-6 text-slate-800">
              {channelCopy.hint}
            </p>
            {errorMessage || screenshotIntake.pasteError ? (
              <p role="alert" className="mx-5 mb-2 shrink-0 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-sm leading-5 text-[var(--danger-fg)]">
                {errorMessage ?? screenshotIntake.pasteError}
              </p>
            ) : null}

            <div className="flex shrink-0 flex-col items-center gap-1.5 bg-[var(--panel)] px-5 pt-1 pb-[max(1.25rem,calc(0.75rem+env(safe-area-inset-bottom,0px)))] sm:pb-5">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (isDesktopCompose) {
                    setMode("composer");
                    return;
                  }
                  fileInputRef.current?.click();
                }}
                className="btn-primary w-[200px] min-h-11 py-2 text-[14px]"
              >
                <Images size={15} strokeWidth={2.25} />
                {ocrProgressLabel(isUploading, uploadProgress, "上传图片")}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setFormError(null);
                  setMode("manual");
                }}
                className="btn-ghost w-[200px] min-h-11 py-1.5 text-[13px]"
              >
                <PenLine size={15} strokeWidth={2.25} />
                手动输入
              </button>
            </div>
          </>
        ) : mode === "composer" ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <ScreenshotComposerGrid
                items={screenshotIntake.items}
                disabled={busy}
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
                disabled={busy || screenshotIntake.items.length === 0}
                onClick={() => onUpload(screenshotIntake.files)}
                className="btn-primary w-[200px] min-h-11 py-2 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
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
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
              <div className="space-y-3">
                {entries.map((entry) => (
                  <ManualEntryCard
                    key={entry.id}
                    entry={entry}
                    canRemove={entries.length > 1}
                    onChange={(patch) => updateEntry(entry.id, patch)}
                    onRemove={() => removeEntry(entry.id)}
                  />
                ))}
              </div>

              <button
                type="button"
                disabled={busy}
                onClick={() => setEntries((current) => [...current, createManualEntry()])}
                className="mt-3 flex min-h-11 w-full items-center justify-end gap-1.5 rounded-xl px-2 text-sm font-bold text-[var(--brand)] transition hover:bg-[var(--brand-soft)] hover:text-[var(--brand-strong)] disabled:opacity-50"
              >
                <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--brand)]">
                  <Plus size={12} strokeWidth={2.5} />
                </span>
                继续添加
              </button>

              {formError || errorMessage ? (
                <p role="alert" className="mt-3 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-xs text-[var(--danger-fg)]">
                  {formError ?? errorMessage}
                </p>
              ) : null}
            </div>

            <div className="border-t border-slate-200/70 bg-[#f5f7fa] px-5 pb-8 pt-4">
              <button
                type="button"
                disabled={!canSubmit}
                onClick={() => void handleManualSubmit()}
                className={`w-full rounded-[var(--radius-control)] px-4 py-4 text-[16px] font-bold transition ${
                  canSubmit
                    ? "bg-[var(--brand-deep)] text-white shadow-[var(--shadow-sm)] hover:bg-[var(--brand-strong)]"
                    : "bg-[#d9e8ff] text-[#8eb3ef]"
                } disabled:cursor-not-allowed`}
              >
                {isSubmitting ? "保存中..." : `保存（${validCount}）`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ManualEntryCard({
  entry,
  canRemove,
  onChange,
  onRemove,
}: {
  entry: ManualEntry;
  canRemove: boolean;
  onChange: (patch: Partial<ManualEntry>) => void;
  onRemove: () => void;
}) {
  const summary =
    entry.fund_name.trim() ||
    entry.holding_amount.trim() ||
    "未填写基金";

  return (
    <div className="relative rounded-2xl bg-white px-4 pb-3 pt-4 shadow-[0_2px_12px_rgba(15,23,42,0.06)]">
      {canRemove ? (
        <button
          type="button"
          onClick={onRemove}
          className="touch-target absolute right-1.5 top-1.5 inline-flex items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-600"
          aria-label="删除此条"
        >
          <X size={16} />
        </button>
      ) : null}

      {entry.collapsed ? (
        <button
          type="button"
          onClick={() => onChange({ collapsed: false })}
          className="flex w-full items-center justify-between rounded-xl bg-[#f0f2f5] px-4 py-3.5 text-left"
        >
          <span className="truncate pr-8 text-sm font-medium text-slate-800">{summary}</span>
          <ChevronDown size={18} className="shrink-0 text-[var(--brand)]" />
        </button>
      ) : (
        <>
          <ManualRow
            label="基金名称"
            value={entry.fund_name}
            placeholder="输入代码或名称"
            onChange={(value) => onChange({ fund_name: value })}
          />
          <ManualRow
            label="持有金额"
            value={entry.holding_amount}
            placeholder="输入金额"
            inputMode="decimal"
            onChange={(value) => onChange({ holding_amount: value })}
          />
          <ManualRow
            label="持有收益"
            value={entry.holding_profit}
            placeholder="选填"
            inputMode="decimal"
            onChange={(value) => onChange({ holding_profit: value })}
          />
          <button
            type="button"
            onClick={() => onChange({ collapsed: true })}
            className="mt-1 flex min-h-11 w-full flex-col items-center justify-center gap-0.5 rounded-xl text-xs font-medium text-[var(--brand)] hover:bg-[var(--brand-soft)]"
          >
            <ChevronUp size={16} strokeWidth={2.25} />
            收起
          </button>
        </>
      )}
    </div>
  );
}

function ManualRow({
  label,
  value,
  placeholder,
  onChange,
  inputMode,
}: {
  label: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  inputMode?: "decimal" | "text";
}) {
  return (
    <label className="mb-2 flex min-h-11 items-center gap-3 rounded-xl bg-[#f0f2f5] px-4 py-3.5 last:mb-0">
      <span className="shrink-0 text-[15px] font-medium text-slate-800">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        inputMode={inputMode}
        className="min-w-0 flex-1 bg-transparent text-right text-[14px] text-slate-900 outline-none placeholder:text-slate-500"
      />
    </label>
  );
}

function isEntryValid(entry: ManualEntry): boolean {
  const fundName = entry.fund_name.trim();
  const amount = Number(entry.holding_amount);
  return Boolean(fundName) && Number.isFinite(amount) && amount > 0;
}

function entryToHolding(entry: ManualEntry): Holding | null {
  if (!isEntryValid(entry)) {
    return null;
  }

  const rawName = entry.fund_name.trim();
  const amount = Number(entry.holding_amount);
  const profitText = entry.holding_profit.trim();
  const holdingProfit = profitText === "" ? null : Number(profitText);

  if (profitText !== "" && !Number.isFinite(holdingProfit)) {
    return null;
  }

  const isCode = /^\d{6}$/.test(rawName);
  const fundCode = isCode ? rawName : "000000";
  const fundName = isCode ? rawName : rawName;
  const returnPercent =
    holdingProfit != null && amount > 0
      ? Math.round((holdingProfit / (amount - holdingProfit)) * 10000) / 100
      : 0;

  return {
    fund_code: fundCode,
    fund_name: fundName,
    holding_amount: amount,
    return_percent: Number.isFinite(returnPercent) ? returnPercent : 0,
    holding_profit: holdingProfit,
    holding_return_percent: returnPercent,
  };
}

