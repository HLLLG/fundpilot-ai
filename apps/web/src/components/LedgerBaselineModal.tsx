"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BookCheck, CheckCircle2, Loader2, ShieldCheck, X } from "lucide-react";
import {
  confirmPortfolioLedgerBaseline,
  fetchPortfolioLedgerBaseline,
  type Holding,
  type PortfolioLedgerBaselineStatus,
} from "@/lib/api";
import { useDialogA11y } from "@/lib/useDialogA11y";

type LedgerBaselineModalProps = {
  open: boolean;
  holdings: Holding[];
  onClose: () => void;
  onConfirmed?: (status: PortfolioLedgerBaselineStatus) => void | Promise<void>;
};

type PositionDraft = {
  fundCode: string;
  fundName: string;
  shares: string;
  costBasis: string;
  suggestedShares: string | null;
  suggestedCost: string | null;
};

function localIsoDate() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function inputNumber(value: string): number | null {
  const normalized = value.replace(/,/g, "").trim();
  if (!normalized) return null;
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function plainValue(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return String(parsed);
}

function buildDrafts(
  holdings: Holding[],
  status: PortfolioLedgerBaselineStatus | null,
): PositionDraft[] {
  const positions = new Map((status?.positions ?? []).map((item) => [item.fund_code, item]));
  return holdings
    .filter((holding) => holding.fund_code && holding.fund_code !== "000000")
    .map((holding) => {
      const current = positions.get(holding.fund_code);
      const suggestedShares = plainValue(current?.settled_shares);
      const suggestedCost = plainValue(current?.cost_basis_total_cny);
      return {
        fundCode: holding.fund_code,
        fundName: holding.fund_name,
        shares: suggestedShares ?? "",
        costBasis: "",
        suggestedShares,
        suggestedCost,
      };
    });
}

export function LedgerBaselineModal({
  open,
  holdings,
  onClose,
  onConfirmed,
}: LedgerBaselineModalProps) {
  const [status, setStatus] = useState<PortfolioLedgerBaselineStatus | null>(null);
  const [drafts, setDrafts] = useState<PositionDraft[]>([]);
  const [asOfDate, setAsOfDate] = useState(localIsoDate);
  const [cashBalance, setCashBalance] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const holdingKey = useMemo(
    () => holdings.map((holding) => `${holding.fund_code}:${holding.fund_name}`).join("|"),
    [holdings],
  );
  const requestClose = () => {
    if (!saving) onClose();
  };
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open,
    onClose: requestClose,
    initialFocusRef: closeButtonRef,
  });

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setAcknowledged(false);
    setAsOfDate(localIsoDate());
    void fetchPortfolioLedgerBaseline()
      .then((nextStatus) => {
        if (cancelled) return;
        setStatus(nextStatus);
        setDrafts(buildDrafts(holdings, nextStatus));
        const knownCash = nextStatus.cash?.status === "known"
          ? plainValue(nextStatus.cash.balance_cny)
          : null;
        setCashBalance(knownCash ?? "");
      })
      .catch((loadError) => {
        if (cancelled) return;
        setStatus(null);
        setDrafts(buildDrafts(holdings, null));
        setCashBalance("");
        setError(loadError instanceof Error ? loadError.message : "账本状态加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [holdingKey, holdings, open]);

  if (!open) return null;

  function updateDraft(index: number, patch: Partial<PositionDraft>) {
    setDrafts((current) => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )));
  }

  async function handleConfirm() {
    const positions = drafts.map((item) => ({
      fund_code: item.fundCode,
      confirmed_shares: inputNumber(item.shares),
      cost_basis_total_yuan: inputNumber(item.costBasis),
    }));
    if (!positions.length) {
      setError("当前没有可确认的基金持仓。");
      return;
    }
    if (positions.some((item) => item.confirmed_shares === null || item.confirmed_shares <= 0)) {
      setError("请为每只基金填写大于 0 的实际持有份额。");
      return;
    }
    const cash = inputNumber(cashBalance);
    if (cash !== null && cash < 0) {
      setError("可用现金不能小于 0；未知时请留空。");
      return;
    }
    if (!acknowledged) {
      setError("请确认已对照原平台核对份额。");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const nextStatus = await confirmPortfolioLedgerBaseline({
        as_of_date: asOfDate,
        cash_balance_yuan: cash,
        positions: positions.map((item) => ({
          fund_code: item.fund_code,
          confirmed_shares: item.confirmed_shares as number,
          cost_basis_total_yuan: item.cost_basis_total_yuan,
        })),
      });
      setStatus(nextStatus);
      await onConfirmed?.(nextStatus);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "账本基线保存失败");
    } finally {
      setSaving(false);
    }
  }

  const isConfirmed = status?.status === "confirmed";

  return (
    <div
      className="fixed inset-0 z-[90] flex items-end justify-center bg-slate-950/55 backdrop-blur-[2px] sm:items-center sm:p-5"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ledger-baseline-title"
        className="flex max-h-[94vh] w-full max-w-3xl flex-col overflow-hidden rounded-t-[30px] bg-[#f4f2eb] shadow-2xl sm:rounded-[30px]"
      >
        <header className="relative overflow-hidden bg-slate-950 px-5 pb-5 pt-4 text-white sm:px-7">
          <div className="pointer-events-none absolute -right-12 -top-20 h-52 w-52 rounded-full border border-emerald-300/15 bg-emerald-300/5" />
          <div className="relative flex items-start justify-between gap-4">
            <div className="flex min-w-0 gap-3">
              <div className="mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-emerald-300/25 bg-emerald-300/10 text-emerald-300">
                <BookCheck size={22} />
              </div>
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.24em] text-emerald-300">
                  Decision ledger · v1
                </p>
                <h2 id="ledger-baseline-title" className="mt-1 text-xl font-black tracking-tight">
                  确认决策账本基线
                </h2>
                <p className="mt-1 max-w-xl text-xs leading-5 text-slate-300">
                  这组数据只需在首次启用或与原平台对账后确认。净值变化不会改动份额账本。
                </p>
              </div>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={requestClose}
              disabled={saving}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-300 transition hover:bg-white/10 hover:text-white disabled:opacity-40"
              aria-label="关闭"
            >
              <X size={20} />
            </button>
          </div>
          <div className="relative mt-4 flex flex-wrap items-center gap-2 text-[11px]">
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 font-bold ${
              isConfirmed
                ? "border-emerald-300/30 bg-emerald-300/10 text-emerald-200"
                : "border-amber-300/30 bg-amber-300/10 text-amber-100"
            }`}>
              {isConfirmed ? <CheckCircle2 size={13} /> : <ShieldCheck size={13} />}
              {isConfirmed ? "已有确认基线，可重新对账" : "当前份额仍含估算值"}
            </span>
            {status?.ledger_version ? (
              <span className="font-mono text-slate-400">{status.ledger_version}</span>
            ) : null}
          </div>
        </header>

        <div className="overflow-y-auto px-4 py-5 sm:px-7">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-sm font-semibold text-slate-500" role="status">
              <Loader2 size={17} className="animate-spin" />
              正在核对当前账本…
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-3 rounded-2xl border border-stone-200 bg-white/80 p-4 sm:grid-cols-2">
                <label className="text-xs font-bold text-slate-700">
                  持仓生效日
                  <input
                    type="date"
                    value={asOfDate}
                    max={localIsoDate()}
                    onChange={(event) => setAsOfDate(event.target.value)}
                    disabled={saving}
                    className="input-field mt-2 w-full bg-white disabled:opacity-60"
                  />
                </label>
                <label className="text-xs font-bold text-slate-700">
                  可用现金（可选）
                  <input
                    type="text"
                    inputMode="decimal"
                    value={cashBalance}
                    onChange={(event) => setCashBalance(event.target.value)}
                    disabled={saving}
                    placeholder="未知请留空，不会按 0 处理"
                    aria-label="可用现金"
                    className="input-field mt-2 w-full bg-white disabled:opacity-60"
                  />
                </label>
              </div>

              <div className="space-y-3">
                {drafts.map((item, index) => (
                  <section
                    key={item.fundCode}
                    className="rounded-2xl border border-stone-200 bg-white px-4 py-4 shadow-[0_10px_30px_rgba(15,23,42,0.04)]"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2 border-b border-dashed border-stone-200 pb-3">
                      <div>
                        <div className="text-sm font-black text-slate-950">{item.fundName}</div>
                        <div className="mt-0.5 font-mono text-[11px] text-slate-500">{item.fundCode}</div>
                      </div>
                      <span className="rounded-full bg-stone-100 px-2.5 py-1 text-[10px] font-bold text-stone-600">
                        原平台真值
                      </span>
                    </div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <label className="text-xs font-bold text-slate-700">
                        实际持有份额 <span className="text-rose-600">*</span>
                        <input
                          type="text"
                          inputMode="decimal"
                          value={item.shares}
                          onChange={(event) => updateDraft(index, { shares: event.target.value })}
                          disabled={saving}
                          aria-label={`${item.fundName}实际持有份额`}
                          placeholder="请输入原平台显示的份额"
                          className="input-field mt-2 w-full bg-white disabled:opacity-60"
                        />
                        {item.suggestedShares ? (
                          <span className="mt-1.5 block font-normal text-slate-500">
                            系统当前估算 {item.suggestedShares} 份，请对照后修正
                          </span>
                        ) : null}
                      </label>
                      <label className="text-xs font-bold text-slate-700">
                        成本总额（可选）
                        <input
                          type="text"
                          inputMode="decimal"
                          value={item.costBasis}
                          onChange={(event) => updateDraft(index, { costBasis: event.target.value })}
                          disabled={saving}
                          aria-label={`${item.fundName}成本总额`}
                          placeholder="未核对请留空"
                          className="input-field mt-2 w-full bg-white disabled:opacity-60"
                        />
                        {item.suggestedCost ? (
                          <span className="mt-1.5 block font-normal text-slate-500">
                            系统估算成本 ¥{item.suggestedCost}，不会自动当作真实成本
                          </span>
                        ) : null}
                      </label>
                    </div>
                  </section>
                ))}
              </div>

              <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50/80 px-4 py-3 text-xs leading-5 text-emerald-950">
                <input
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                  disabled={saving}
                  className="mt-1 h-4 w-4 rounded border-emerald-300 text-emerald-700 focus:ring-emerald-600"
                />
                <span>
                  <strong className="block font-black">我已对照原平台核对实际份额</strong>
                  成本和现金若未核对可以留空，系统会明确标记为未知，不会猜成 0。
                </span>
              </label>

              {error ? (
                <p className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700" role="alert">
                  {error}
                </p>
              ) : null}
            </div>
          )}
        </div>

        <footer className="border-t border-stone-200 bg-white px-4 py-4 sm:px-7">
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={requestClose}
              disabled={saving}
              className="btn-secondary min-h-11 sm:min-w-28"
            >
              稍后确认
            </button>
            <button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={loading || saving || !drafts.length || !acknowledged}
              className="btn-primary min-h-11 sm:min-w-44 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {saving ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
              {saving ? "正在冻结基线…" : "确认并冻结账本"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
