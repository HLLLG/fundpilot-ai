"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import type { Holding, HoldingAdjustmentPatch } from "@/lib/api";
import { getSettledHoldingAmount, getEstimatedHoldingProfit } from "@/lib/holdingDisplay";
import { useDialogA11y } from "@/lib/useDialogA11y";

type HoldingModifyModalProps = {
  open: boolean;
  holding: Holding;
  holdingDays?: number | null;
  onClose: () => void;
  onSubmit: (patch: HoldingAdjustmentPatch) => void | Promise<void>;
  onEditPurchaseDate?: () => void;
  onSyncBuy?: () => void;
  onSyncSell?: () => void;
};

function parseMoneyInput(value: string): number | null {
  const trimmed = value.replace(/,/g, "").trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number.parseFloat(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function HoldingModifyModal({
  open,
  holding,
  holdingDays,
  onClose,
  onSubmit,
  onEditPurchaseDate,
  onSyncBuy,
  onSyncSell,
}: HoldingModifyModalProps) {
  const settled = getSettledHoldingAmount(holding);
  const profit = getEstimatedHoldingProfit(holding);

  const [amountInput, setAmountInput] = useState(String(settled));
  const [profitInput, setProfitInput] = useState(profit != null ? String(profit) : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editingFundCodeRef = useRef<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const requestClose = () => {
    if (!saving) {
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
      editingFundCodeRef.current = null;
      return;
    }
    if (editingFundCodeRef.current === holding.fund_code) {
      return;
    }
    editingFundCodeRef.current = holding.fund_code;
    setAmountInput(String(settled));
    setProfitInput(profit != null ? String(profit) : "");
    setError(null);
  }, [holding.fund_code, open, profit, settled]);

  if (!open) {
    return null;
  }

  async function handleSave() {
    const amount = parseMoneyInput(amountInput);
    const profitValue = parseMoneyInput(profitInput);
    if (amount == null || amount <= 0) {
      setError("持有金额必须大于 0；如已清仓，请使用删除该基金");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await onSubmit({
        settled_holding_amount: amount,
        holding_profit: profitValue,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[75] flex items-end justify-center bg-slate-950/40 sm:items-center sm:p-4"
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
        aria-labelledby="holding-modify-title"
      >
        <header className="relative flex items-center justify-center border-b border-slate-200/70 bg-white px-4 py-3.5">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={requestClose}
            disabled={saving}
            className="absolute left-3 inline-flex h-11 w-11 items-center justify-center rounded-full text-slate-600 hover:bg-slate-100"
            aria-label="返回"
          >
            <ChevronLeft size={22} />
          </button>
          <h2 id="holding-modify-title" className="text-base font-bold text-slate-900">
            支付宝-修改持仓
          </h2>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          <div className="rounded-2xl bg-white px-4 py-3">
            <div className="text-sm font-bold text-slate-900">{holding.fund_name}</div>
            <div className="mt-1 text-xs text-slate-500">{holding.fund_code}</div>
          </div>

          <label className="block rounded-2xl bg-white px-4 py-3">
            <span className="text-sm font-semibold text-slate-800">持有金额</span>
            <input
              value={amountInput}
              onChange={(event) => setAmountInput(event.target.value)}
              disabled={saving}
              inputMode="decimal"
              className="input-field mt-2 w-full text-right text-lg font-bold tabular-nums disabled:cursor-wait disabled:opacity-60"
            />
          </label>

          <label className="block rounded-2xl bg-white px-4 py-3">
            <span className="text-sm font-semibold text-slate-800">持有收益</span>
            <input
              value={profitInput}
              onChange={(event) => setProfitInput(event.target.value)}
              disabled={saving}
              inputMode="decimal"
              className="input-field mt-2 w-full text-right text-lg font-bold tabular-nums disabled:cursor-wait disabled:opacity-60"
            />
          </label>

          <button
            type="button"
            onClick={onEditPurchaseDate}
            disabled={saving}
            className="flex min-h-11 w-full items-center justify-between rounded-2xl bg-white px-4 py-3 text-left disabled:cursor-wait disabled:opacity-60"
          >
            <span className="text-sm font-semibold text-slate-800">持有天数</span>
            <span className="flex items-center gap-1 text-sm font-bold text-slate-900">
              {holdingDays != null ? `${holdingDays}天` : "—"}
              <ChevronRight size={16} className="text-slate-300" />
            </span>
          </button>

          <div className="grid grid-cols-2 gap-3 pt-1">
            <button
              type="button"
              onClick={onSyncBuy}
              disabled={saving}
              className="min-h-11 rounded-2xl bg-white py-4 text-center text-base font-bold text-rose-600 shadow-sm hover:bg-rose-50 disabled:cursor-wait disabled:opacity-60"
            >
              同步加仓
            </button>
            <button
              type="button"
              onClick={onSyncSell}
              disabled={saving}
              className="min-h-11 rounded-2xl bg-white py-4 text-center text-base font-bold text-emerald-700 shadow-sm hover:bg-emerald-50 disabled:cursor-wait disabled:opacity-60"
            >
              同步减仓
            </button>
          </div>

          {error ? (
            <p className="text-sm font-medium text-rose-600" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        <div className="border-t border-slate-200 bg-white px-4 py-4">
          <button
            type="button"
            disabled={saving}
            onClick={() => void handleSave()}
            className="btn-primary min-h-11 w-full !py-3.5"
          >
            {saving ? (
              <>
                <Loader2 size={16} className="mr-2 inline animate-spin" />
                保存中…
              </>
            ) : (
              "保存修改"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
