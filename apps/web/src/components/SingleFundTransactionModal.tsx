"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronLeft, Loader2, ShieldCheck } from "lucide-react";
import {
  fetchPortfolioLedgerBaseline,
  type FundTradeability,
  type Holding,
  type ParsedTransaction,
} from "@/lib/api";
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
  reviewTargetAmountYuan?: number | null;
  tradeability?: FundTradeability | null;
  requireRedemptionReview?: boolean;
  onClose: () => void;
  onSubmit: (transaction: ParsedTransaction) => void | Promise<void>;
};

function localTradeDate(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function currentTradeTiming(): TradeTiming {
  const now = new Date();
  return now.getHours() < 15 ? "before_close" : "after_close";
}

function buildTradeTime(tradeDate: string, timing: TradeTiming): string {
  return timing === "after_close"
    ? `${tradeDate} 15:30:00`
    : `${tradeDate} 14:30:00`;
}

function feeTierText(tier: NonNullable<FundTradeability["redemption_fee_tiers"]>[number]) {
  const fee = tier.fee_percent != null
    ? `${tier.fee_percent}%`
    : tier.flat_fee_yuan != null
      ? `¥${formatPlainMoney(tier.flat_fee_yuan)}`
      : "费率待核对";
  return `${tier.condition || "适用条件以平台为准"} · ${fee}`;
}

export function SingleFundTransactionModal({
  open,
  holding,
  direction,
  maxShares,
  latestNav,
  navDateLabel,
  reviewTargetAmountYuan,
  tradeability,
  requireRedemptionReview = false,
  onClose,
  onSubmit,
}: SingleFundTransactionModalProps) {
  const [sharesInput, setSharesInput] = useState("");
  const [feeInput, setFeeInput] = useState("");
  const [tradeDate, setTradeDate] = useState(localTradeDate);
  const [timing, setTiming] = useState<TradeTiming>(currentTradeTiming);
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false);
  const [ledgerMaxShares, setLedgerMaxShares] = useState<number | null>(null);
  const [loadingLedgerShares, setLoadingLedgerShares] = useState(false);
  const [ledgerSharesError, setLedgerSharesError] = useState<string | null>(null);
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
  const title = isSell
    ? requireRedemptionReview ? "核对并记录减仓" : "支付宝-同步减仓"
    : "支付宝-同步加仓";
  const max = maxShares ?? ledgerMaxShares;

  useEffect(() => {
    if (!open) {
      return;
    }
    setSharesInput("");
    setFeeInput("");
    setTradeDate(localTradeDate());
    setTiming(currentTradeTiming());
    setReviewAcknowledged(false);
    setError(null);
  }, [open, direction]);

  useEffect(() => {
    if (!open || !isSell || maxShares != null) {
      setLedgerMaxShares(null);
      setLedgerSharesError(null);
      setLoadingLedgerShares(false);
      return;
    }
    let cancelled = false;
    setLoadingLedgerShares(true);
    setLedgerSharesError(null);
    void fetchPortfolioLedgerBaseline()
      .then((status) => {
        if (cancelled) return;
        const position = status.positions.find((item) => item.fund_code === holding.fund_code);
        const shares = Number(position?.settled_shares);
        if (Number.isFinite(shares) && shares > 0) {
          setLedgerMaxShares(shares);
        } else {
          setLedgerSharesError("实际持有份额尚未确认");
        }
      })
      .catch(() => {
        if (!cancelled) setLedgerSharesError("实际持有份额加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoadingLedgerShares(false);
      });
    return () => {
      cancelled = true;
    };
  }, [holding.fund_code, isSell, maxShares, open]);

  if (!open) {
    return null;
  }

  const parsedShares = Number.parseFloat(sharesInput.replace(/,/g, "").trim());
  const parsedFee = feeInput.trim()
    ? Number.parseFloat(feeInput.replace(/,/g, "").trim())
    : null;
  const amountYuan =
    Number.isFinite(parsedShares) && latestNav && latestNav > 0
      ? Math.round(parsedShares * latestNav * 100) / 100
      : null;
  const targetShares =
    reviewTargetAmountYuan && latestNav && latestNav > 0
      ? reviewTargetAmountYuan / latestNav
      : null;
  const redemptionFeeTiers = tradeability?.redemption_fee_tiers?.slice(0, 4) ?? [];

  async function handleConfirm() {
    if (!Number.isFinite(parsedShares) || parsedShares <= 0) {
      setError("请输入有效份额");
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(tradeDate)) {
      setError("请选择实际操作日期");
      return;
    }
    if (requireRedemptionReview && !reviewAcknowledged) {
      setError("请先确认已在支付宝核对赎回条件与适用费率");
      return;
    }
    if (isSell && requireRedemptionReview && max == null) {
      setError("实际持有份额尚未确认，请先核对账本基线");
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
    if (parsedFee != null && (!Number.isFinite(parsedFee) || parsedFee < 0)) {
      setError("赎回费不能小于 0");
      return;
    }

    const tx: ParsedTransaction = {
      direction,
      fund_name: holding.fund_name,
      fund_code: holding.fund_code,
      amount_yuan: amountYuan,
      confirmed_shares: parsedShares,
      fee_yuan: parsedFee,
      trade_time: buildTradeTime(tradeDate, timing),
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

          {isSell && reviewTargetAmountYuan != null ? (
            <div className="rounded-2xl border border-orange-200 bg-orange-50 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-bold text-orange-800">目标减仓市值</span>
                <span className="rounded-full bg-white px-2 py-1 text-[10px] font-black text-orange-700">
                  待核对
                </span>
              </div>
              <div className="mt-1 text-2xl font-black tabular-nums text-slate-950">
                ¥{reviewTargetAmountYuan.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}
              </div>
              {targetShares != null ? (
                <p className="mt-1 text-xs text-orange-900/75">
                  按最新净值约 {formatPlainMoney(targetShares)} 份，实际入账以支付宝卖出份额为准
                </p>
              ) : null}
            </div>
          ) : null}

          {isSell && requireRedemptionReview ? (
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
              <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
                <ShieldCheck size={17} className="text-emerald-700" />
                赎回条件
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-[11px] font-bold">
                <span className={`rounded-full px-2.5 py-1 ${
                  tradeability?.redemption_state === "open"
                    ? "bg-emerald-50 text-emerald-800"
                    : "bg-amber-50 text-amber-800"
                }`}>
                  {tradeability?.redemption_state === "open" ? "赎回开放" : "赎回状态待核对"}
                </span>
                <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">
                  逐笔持有期以支付宝为准
                </span>
              </div>
              {redemptionFeeTiers.length ? (
                <div className="mt-3 space-y-1.5 rounded-xl bg-slate-50 px-3 py-2.5 text-xs text-slate-700">
                  {redemptionFeeTiers.map((tier, index) => (
                    <div key={`${tier.condition ?? "tier"}-${index}`}>{feeTierText(tier)}</div>
                  ))}
                </div>
              ) : (
                <p className="mt-3 text-xs text-slate-500">费率档位未完整获取，请以支付宝订单页为准</p>
              )}
              <label className="mt-3 flex cursor-pointer items-start gap-2.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-xs leading-5 text-emerald-950">
                <input
                  type="checkbox"
                  checked={reviewAcknowledged}
                  onChange={(event) => setReviewAcknowledged(event.target.checked)}
                  disabled={saving}
                  className="mt-0.5 h-4 w-4 rounded border-emerald-300 text-emerald-700"
                />
                <span>我已在支付宝确认可赎回，并核对锁定期与适用费率</span>
              </label>
            </div>
          ) : null}

          <div className="rounded-2xl bg-white px-4 py-4">
            <div className="text-sm font-semibold text-slate-800">
              {isSell ? "同步卖出份额" : "同步买入份额"}
            </div>
            {isSell && max != null ? (
              <p className="mt-1 text-xs text-slate-500">最多可选 {formatPlainMoney(max)} 份</p>
            ) : null}
            {isSell && loadingLedgerShares ? (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
                <Loader2 size={12} className="animate-spin" /> 正在读取账本份额
              </p>
            ) : null}
            {isSell && ledgerSharesError ? (
              <p className="mt-1 text-xs font-medium text-amber-700">{ledgerSharesError}</p>
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
            {isSell ? (
              <label className="mt-4 block text-xs font-semibold text-slate-700">
                实际赎回费（可选）
                <input
                  value={feeInput}
                  onChange={(event) => setFeeInput(event.target.value)}
                  disabled={saving}
                  inputMode="decimal"
                  aria-label="实际赎回费"
                  placeholder="订单未显示可留空"
                  className="input-field mt-2 w-full disabled:cursor-wait disabled:opacity-60"
                />
              </label>
            ) : null}
          </div>

          <div className="rounded-2xl bg-white px-4 py-3">
            <div className="text-sm font-semibold text-slate-800">原平台成交时间</div>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <input
                type="date"
                value={tradeDate}
                max={localTradeDate()}
                onChange={(event) => setTradeDate(event.target.value)}
                disabled={saving}
                aria-label="原平台成交日期"
                className="input-field w-full disabled:cursor-wait disabled:opacity-60"
              />
              <select
                value={timing}
                onChange={(event) => setTiming(event.target.value as TradeTiming)}
                disabled={saving}
                aria-label="原平台成交时间"
                className="input-field w-full disabled:cursor-wait disabled:opacity-60"
              >
                <option value="before_close">下午 3 点前</option>
                <option value="after_close">下午 3 点后</option>
              </select>
            </div>
            <p className="mt-2 text-[11px] text-slate-500">系统按操作时间和交易日历自动确定持仓生效日</p>
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
            disabled={saving || (requireRedemptionReview && loadingLedgerShares)}
            onClick={() => void handleConfirm()}
            className="btn-primary min-h-11 w-full !py-3.5"
          >
            {saving ? (
              <>
                <Loader2 size={16} className="mr-2 inline animate-spin" />
                提交中…
              </>
            ) : (
              requireRedemptionReview ? (
                <><CheckCircle2 size={16} className="mr-2 inline" />记录实际减仓</>
              ) : "确认"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
