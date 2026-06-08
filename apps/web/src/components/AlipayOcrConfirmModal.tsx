"use client";

import { X } from "lucide-react";
import type { Holding } from "@/lib/api";
import { cnProfitClass, formatSignedMoney } from "@/lib/holdingMetrics";

type FundCodeResolution = {
  fund_name: string;
  fund_code: string | null;
  source: string | null;
  resolved: boolean;
};

type AlipayOcrConfirmModalProps = {
  holdings: Holding[];
  fundCodeResolutions?: FundCodeResolution[];
  amountSemanticsNote?: string | null;
  isBusy?: boolean;
  onChange: (holdings: Holding[]) => void;
  onConfirm: () => void;
  onClose: () => void;
};

function formatAmount(value: number) {
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function AlipayOcrConfirmModal({
  holdings,
  fundCodeResolutions = [],
  amountSemanticsNote,
  isBusy = false,
  onChange,
  onConfirm,
  onClose,
}: AlipayOcrConfirmModalProps) {
  const resolutionByName = new Map(fundCodeResolutions.map((item) => [item.fund_name, item]));

  const removeAt = (index: number) => {
    onChange(holdings.filter((_, itemIndex) => itemIndex !== index));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-4 sm:items-center">
      <div className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-[28px] bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-lg font-black text-slate-950">确认识别结果</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              核对基金代码、持有金额与持有收益后，点击完成更新账户汇总。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        {amountSemanticsNote ? (
          <div className="border-b border-blue-100 bg-blue-50 px-5 py-3 text-xs leading-5 text-blue-800">
            {amountSemanticsNote}
          </div>
        ) : null}

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {holdings.map((holding, index) => {
            const resolution = resolutionByName.get(holding.fund_name);
            const code = holding.fund_code !== "000000" ? holding.fund_code : resolution?.fund_code;
            const unresolved = !code;

            return (
              <div
                key={`${holding.fund_name}-${index}`}
                className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3"
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-slate-400">
                      {unresolved ? "待匹配代码" : code}
                      {resolution?.source ? ` · ${resolution.source}` : null}
                    </div>
                    <div className="mt-1 truncate text-sm font-black text-slate-950">{holding.fund_name}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeAt(index)}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 transition hover:bg-white hover:text-rose-600"
                    aria-label="移除"
                  >
                    <X size={16} />
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-[11px] font-semibold text-slate-400">持有金额</div>
                    <div className="mt-0.5 font-black tabular-nums text-slate-950">
                      {formatAmount(holding.holding_amount)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[11px] font-semibold text-slate-400">持有收益</div>
                    <div
                      className={`mt-0.5 font-black tabular-nums ${cnProfitClass(holding.holding_profit)}`}
                    >
                      {formatSignedMoney(holding.holding_profit)}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="border-t border-slate-100 px-4 py-4">
          <button
            type="button"
            disabled={isBusy || holdings.length === 0}
            onClick={onConfirm}
            className="w-full rounded-2xl bg-blue-600 px-4 py-3 text-sm font-black text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isBusy ? "正在更新..." : `完成（${holdings.length}）`}
          </button>
        </div>
      </div>
    </div>
  );
}
