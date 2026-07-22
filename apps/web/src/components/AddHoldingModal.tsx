"use client";

import Image from "next/image";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  PenLine,
  Plus,
  X,
} from "lucide-react";
import type { Holding } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";
import { useDialogA11y } from "@/lib/useDialogA11y";

const ALIPAY_GUIDE_IMAGE = "/guides/alipay-holdings-overview.png";

const ALIPAY_CHANNEL_COPY: { title: string; hint: ReactNode } = {
  title: "导入持有",
  hint: (
    <>
      上传支付宝
      <span className="font-bold text-[var(--brand)]">「我的持有」</span>
      总览截图即可同步持仓
    </>
  ),
};

type AddHoldingModalProps = {
  open: boolean;
  onClose: () => void;
  onUpload: (file: File) => void;
  onManualSubmit: (holdings: Holding[]) => void | Promise<void>;
  isUploading?: boolean;
  isSubmitting?: boolean;
  errorMessage?: string | null;
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
  isSubmitting = false,
  errorMessage = null,
}: AddHoldingModalProps) {
  const [mode, setMode] = useState<"chooser" | "manual">("chooser");
  const [entries, setEntries] = useState<ManualEntry[]>([createManualEntry()]);
  const [formError, setFormError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
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

  useEffect(() => {
    if (!open) {
      setMode("chooser");
      setEntries([createManualEntry()]);
      setFormError(null);
    }
  }, [open]);

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
        className="workflow-dialog flex max-h-[94vh] w-full max-w-lg flex-col overflow-hidden rounded-t-[18px] bg-[var(--panel)] shadow-[var(--shadow-lg)] sm:rounded-[18px]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-holding-modal-title"
      >
        <header className="relative flex items-center justify-center border-b border-slate-200/70 bg-white px-4 py-3.5">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={() => {
              if (mode === "manual") {
                setFormError(null);
                setMode("chooser");
                return;
              }
              onClose();
            }}
            disabled={busy}
            className="touch-target absolute left-2 inline-flex items-center justify-center rounded-full text-slate-600 transition hover:bg-slate-100 disabled:opacity-50"
            aria-label={mode === "manual" ? "返回" : "关闭"}
          >
            <ChevronLeft size={22} strokeWidth={2.25} />
          </button>
          <h2 id="add-holding-modal-title" className="text-base font-bold text-slate-900">
            {mode === "manual" ? "手动新增" : channelCopy.title}
          </h2>
        </header>

        <ol className="workflow-rail" aria-label="持仓导入进度">
          <li aria-current="step"><span>01</span><strong>录入基金</strong></li>
          <li><span>02</span><strong>核对数据</strong></li>
          <li><span>03</span><strong>保存持仓</strong></li>
        </ol>

        {mode === "chooser" ? (
          <>
            <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-5 pb-2 pt-6">
              <AlipayPhoneGuide src={ALIPAY_GUIDE_IMAGE} />
              <p className="mt-6 text-center text-[15px] leading-7 text-slate-800">{channelCopy.hint}</p>
              <ol className="mt-5 w-full space-y-2.5">
                {[
                  "打开支付宝 → 我的 → 总资产 → 基金，进入「我的持有」",
                  "截图保存当前持仓总览页",
                  `回到这里上传截图，${BRAND.name}自动识别`,
                ].map((text, index) => (
                  <li key={index} className="flex items-center gap-3">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--brand-soft)] text-xs font-black text-[var(--brand-strong)]">
                      {index + 1}
                    </span>
                    <span className="text-[13px] leading-5 text-slate-600">{text}</span>
                  </li>
                ))}
              </ol>
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
                disabled={busy}
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
                disabled={busy}
                onClick={() => fileInputRef.current?.click()}
                className="btn-primary w-full py-4 text-[16px]"
              >
                {isUploading ? "识别中..." : "去相册选择"}
                {!isUploading ? <ChevronRight size={18} strokeWidth={2.5} /> : null}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setFormError(null);
                  setMode("manual");
                }}
                className="btn-ghost w-full py-3 text-[15px]"
              >
                <PenLine size={18} strokeWidth={2.25} />
                手动输入
              </button>
            </div>
          </>
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

function AlipayPhoneGuide({ src }: { src: string }) {
  return (
    <div className="relative mx-auto w-[62%] min-w-[200px] max-w-[250px]">
      <div className="rounded-[2.25rem] border-[7px] border-slate-900 bg-slate-900 p-[5px] shadow-[0_24px_48px_rgba(15,23,42,0.18)]">
        <div className="relative overflow-hidden rounded-[1.65rem] bg-white">
          <div className="pointer-events-none absolute left-1/2 top-0 z-10 h-[22px] w-[34%] -translate-x-1/2 rounded-b-[14px] bg-slate-900" />
          <Image
            src={src}
            alt="支付宝全部持有页面示意图"
            width={390}
            height={844}
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
