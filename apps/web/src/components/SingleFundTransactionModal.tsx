"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, Loader2 } from "lucide-react";
import type { Holding, ParsedTransaction } from "@/lib/api";
import { formatPlainMoney } from "@/lib/holdingMetrics";
import { useDialogA11y } from "@/lib/useDialogA11y";

type TradeTiming = "before_close" | "after_close";

type SingleFundTransactionModalProps = {
  open: boolean;
  holding: Holding;
  direction: "buy" | "sell";
  maxShares?: number | null;
  latestNav?: number | null;
  navDateLabel?: string | null;
  onClose: () => void;
  onSubmit: (transaction: ParsedTransaction) => void | Promise<void>;
};

function buildTradeTime(timing: TradeTiming): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return timing === "after_close"
    ? `${y}-${m}-${d} 15:30:00`
    : `${y}-${m}-${d} 14:30:00`;
}

export function SingleFundTransactionModal({
  open,
  holding,
  direction,
  maxShares,
  latestNav,
  navDateLabel,
  onClose,
  onSubmit,
}: SingleFundTransactionModalProps) {
  const [sharesInput, setSharesInput] = useState("");
  const [timing, setTiming] = useState<TradeTiming>("after_close");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  const isSell = direction === "sell";
  const title = isSell ? "支付宝-同步减仓" : "支付宝-同步加仓";
  const max = maxShares ?? null;

  useEffect(() => {
    if (!open) {
      return;
    }
    setSharesInput("");
    setTiming("after_close");
    setError(null);
  }, [open, direction]);

  if (!open) {
    return null;
  }

  const parsedShares = Number.parseFloat(sharesInput.replace(/,/g, "").trim());
  const amountYuan =
    Number.isFinite(parsedShares) && latestNav && latestNav > 0
      ? Math.round(parsedShares * latestNav * 100) / 100
      : null;

  async function handleConfirm() {
    if (!Number.isFinite(parsedShares) || parsedShares <= 0) {
      setError("请输入有效份额");
      return;
    }
    if (isSell && max != null && parsedShares > max + 0.001) {
      setError(`最多可卖 ${formatPlainMoney(max)} 份`);
      return;
    }
    if (amountYuan == null || amountYuan <= 0) {
      setError("无法估算成交金额，请稍后重试");
      return;
    }

    const tx: ParsedTransaction = {
      direction,
      fund_name: holding.fund_name,
      fund_code: holding.fund_code,
      amount_yuan: amountYuan,
      trade_time: buildTradeTime(timing),
      confirm_date: null,
      in_progress: false,
    };

    setSaving(true);
    setError(null);
    try {
      await onSubmit(tx);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSaving(false);
    }
  }

  function applyRatio(ratio: number) {
    if (max == null || max <= 0) {
      return;
    }
    setSharesInput(String(Math.round(max * ratio * 100) / 100));
  }

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-slate-950/40 sm:items-center sm:p-4"
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
        aria-labelledby="single-fund-transaction-title"
      >
        <header className="relative flex items-center justify-center border-b border-slate-200/70 bg-white px-4 py-3.5">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={requestClose}
            disabled={saving}
            className="absolute left-3 inline-flex h-11 w-11 items-center justify-center rounded-full text-slate-600 hover:bg-slate-100 disabled:opacity-50"
            aria-label="返回"
          >
            <ChevronLeft size={22} />
          </button>
          <h2 id="single-fund-transaction-title" className="text-base font-bold text-slate-900">
            {title}
          </h2>
        </header>

        <div className="space-y-4 overflow-y-auto px-4 py-4">
          <div className="rounded-2xl bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">
            请确保已在原平台完成{isSell ? "卖出" : "买入"}操作
          </div>

          <div className="rounded-2xl bg-white px-4 py-3">
            <div className="text-sm font-bold text-slate-900">{holding.fund_name}</div>
            <div className="mt-1 text-xs text-slate-500">{holding.fund_code}</div>
            {latestNav != null ? (
              <div className="mt-2 text-xs text-slate-600">
                最新净值{navDateLabel ? ` (${navDateLabel})` : ""}：{latestNav.toFixed(4)}
              </div>
            ) : null}
          </div>

          <div className="rounded-2xl bg-white px-4 py-4">
            <div className="text-sm font-semibold text-slate-800">
              {isSell ? "同步卖出份额" : "同步买入份额"}
            </div>
            {isSell && max != null ? (
              <p className="mt-1 text-xs text-slate-500">最多可选 {formatPlainMoney(max)} 份</p>
            ) : null}
            <input
              value={sharesInput}
              onChange={(event) => setSharesInput(event.target.value)}
              disabled={saving}
              inputMode="decimal"
              aria-label={isSell ? "同步卖出份额" : "同步买入份额"}
              placeholder={isSell && max != null ? `最多 ${formatPlainMoney(max)} 份` : "请输入份额"}
              className="input-field mt-3 w-full text-lg font-bold tabular-nums disabled:cursor-wait disabled:opacity-60"
            />
            {isSell ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {[
                  { label: "1/4", ratio: 0.25 },
                  { label: "1/3", ratio: 1 / 3 },
                  { label: "1/2", ratio: 0.5 },
                  { label: "全部", ratio: 1 },
                ].map((item) => (
                  <button
                    key={item.label}
                    type="button"
                    onClick={() => applyRatio(item.ratio)}
                    disabled={saving}
                    className="min-h-11 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            ) : null}
            {amountYuan != null ? (
              <p className="mt-2 text-xs text-slate-500">约合金额 ¥{formatPlainMoney(amountYuan)}</p>
            ) : null}
          </div>

          <div className="rounded-2xl bg-white px-4 py-3">
            <div className="text-sm font-semibold text-slate-800">原平台成交时间</div>
            <select
              value={timing}
              onChange={(event) => setTiming(event.target.value as TradeTiming)}
              disabled={saving}
              aria-label="原平台成交时间"
              className="input-field mt-2 w-full disabled:cursor-wait disabled:opacity-60"
            >
              <option value="before_close">当天下午 3 点前</option>
              <option value="after_close">当天下午 3 点后</option>
            </select>
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
            onClick={() => void handleConfirm()}
            className="btn-primary min-h-11 w-full !py-3.5"
          >
            {saving ? (
              <>
                <Loader2 size={16} className="mr-2 inline animate-spin" />
                提交中…
              </>
            ) : (
              "确认"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
