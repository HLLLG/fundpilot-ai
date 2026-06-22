"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import type { Holding } from "@/lib/api";
import { adjustHolding } from "@/lib/api";
import { getSettledHoldingAmount, getEstimatedHoldingProfit } from "@/lib/holdingDisplay";

type HoldingModifyModalProps = {
  open: boolean;
  holding: Holding;
  holdingDays?: number | null;
  onClose: () => void;
  onSaved: (payload: { holdings: Holding[] }) => void | Promise<void>;
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
  onSaved,
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

  useEffect(() => {
    if (!open) {
      return;
    }
    setAmountInput(String(getSettledHoldingAmount(holding)));
    const nextProfit = getEstimatedHoldingProfit(holding);
    setProfitInput(nextProfit != null ? String(nextProfit) : "");
    setError(null);
  }, [open, holding]);

  if (!open) {
    return null;
  }

  async function handleSave() {
    const amount = parseMoneyInput(amountInput);
    const profitValue = parseMoneyInput(profitInput);
    if (amount == null || amount < 0) {
      setError("请输入有效的持有金额");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const payload = await adjustHolding(holding.fund_code, {
        settled_holding_amount: amount,
        holding_profit: profitValue,
      });
      await onSaved(payload);
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
      onClick={() => {
        if (!saving) {
          onClose();
        }
      }}
      role="presentation"
    >
      <div
        className="flex max-h-[94vh] w-full max-w-md flex-col overflow-hidden rounded-t-[28px] bg-[#f5f7fa] shadow-2xl sm:rounded-[28px]"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="holding-modify-title"
      >
        <header className="relative flex items-center justify-center border-b border-slate-200/70 bg-white px-4 py-3.5">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="absolute left-3 inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-600 hover:bg-slate-100"
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
              inputMode="decimal"
              className="input-field mt-2 w-full text-right text-lg font-bold tabular-nums"
            />
          </label>

          <label className="block rounded-2xl bg-white px-4 py-3">
            <span className="text-sm font-semibold text-slate-800">持有收益</span>
            <input
              value={profitInput}
              onChange={(event) => setProfitInput(event.target.value)}
              inputMode="decimal"
              className="input-field mt-2 w-full text-right text-lg font-bold tabular-nums"
            />
          </label>

          <button
            type="button"
            onClick={onEditPurchaseDate}
            className="flex w-full items-center justify-between rounded-2xl bg-white px-4 py-3 text-left"
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
              className="rounded-2xl bg-white py-4 text-center text-base font-bold text-rose-600 shadow-sm hover:bg-rose-50"
            >
              同步加仓
            </button>
            <button
              type="button"
              onClick={onSyncSell}
              className="rounded-2xl bg-white py-4 text-center text-base font-bold text-emerald-600 shadow-sm hover:bg-emerald-50"
            >
              同步减仓
            </button>
          </div>

          {error ? <p className="text-sm font-medium text-rose-600">{error}</p> : null}
        </div>

        <div className="border-t border-slate-200 bg-white px-4 py-4">
          <button
            type="button"
            disabled={saving}
            onClick={() => void handleSave()}
            className="btn-primary w-full !py-3.5"
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
