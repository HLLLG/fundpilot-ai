"use client";

import { useEffect, useState } from "react";
import { Plus, Search, X } from "lucide-react";
import type { FundSearchItem, ParsedTransaction } from "@/lib/api";
import { searchFunds } from "@/lib/api";

type BatchTransactionConfirmModalProps = {
  transactions: ParsedTransaction[];
  isBusy?: boolean;
  onChange: (transactions: ParsedTransaction[]) => void;
  onConfirm: () => void;
  onContinueUpload: () => void;
  onClose: () => void;
};

function parseAmountInput(value: string): number {
  const parsed = Number.parseFloat(value.replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function FundCodeSearchPanel({
  initialQuery,
  onSelect,
  onClose,
}: {
  initialQuery: string;
  onSelect: (item: FundSearchItem) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [items, setItems] = useState<FundSearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (query.trim().length < 2) {
        setItems([]);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const results = await searchFunds(query.trim());
        if (!cancelled) {
          setItems(results);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "搜索失败");
          setItems([]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    const timer = window.setTimeout(() => {
      void run();
    }, 280);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  return (
    <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-48 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg">
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="输入基金名称或代码"
          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-blue-400"
          autoFocus
        />
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
        >
          取消
        </button>
      </div>
      {loading ? <div className="px-3 py-3 text-xs text-slate-400">搜索中...</div> : null}
      {error ? <div className="px-3 py-3 text-xs text-rose-600">{error}</div> : null}
      {!loading && !error && items.length === 0 ? (
        <div className="px-3 py-3 text-xs text-slate-400">输入名称或代码搜索</div>
      ) : null}
      {items.map((item) => (
        <button
          key={item.fund_code}
          type="button"
          onClick={() => onSelect(item)}
          className="flex w-full flex-col items-start gap-0.5 border-b border-slate-50 px-3 py-2.5 text-left transition hover:bg-blue-50"
        >
          <span className="text-xs font-bold tabular-nums text-blue-700">{item.fund_code}</span>
          <span className="text-xs text-slate-700">{item.fund_name}</span>
        </button>
      ))}
    </div>
  );
}

export function BatchTransactionConfirmModal({
  transactions,
  isBusy = false,
  onChange,
  onConfirm,
  onContinueUpload,
  onClose,
}: BatchTransactionConfirmModalProps) {
  const [searchIndex, setSearchIndex] = useState<number | null>(null);

  const removeAt = (index: number) => {
    onChange(transactions.filter((_, itemIndex) => itemIndex !== index));
  };

  const updateAt = (index: number, patch: Partial<ParsedTransaction>) => {
    onChange(
      transactions.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );
  };

  const validCount = transactions.filter((tx) => Boolean(tx.fund_code)).length;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-4 sm:items-center">
      <div className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-[28px] bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-lg font-black text-slate-950">新增交易记录-支付宝</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              加仓（红）/减仓（绿）；代码未匹配时点「选择基金」从东财选取；可改方向/金额/时间。
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

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {transactions.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-400">未解析到交易记录。</p>
          ) : null}
          {transactions.map((tx, index) => {
            const isBuy = tx.direction === "buy";
            const unresolved = !tx.fund_code;
            return (
              <div
                key={`${tx.fund_name}-${tx.trade_time}-${index}`}
                className="relative rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3"
              >
                <button
                  type="button"
                  onClick={() => removeAt(index)}
                  className="absolute right-3 top-3 inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-400 transition hover:bg-white hover:text-rose-600"
                  aria-label="移除此条"
                >
                  <X size={15} />
                </button>

                <div className="mb-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      updateAt(index, { direction: isBuy ? "sell" : "buy" })
                    }
                    className={`rounded-md px-2 py-0.5 text-xs font-black transition ${
                      isBuy
                        ? "bg-rose-100 text-rose-600 hover:bg-rose-200"
                        : "bg-emerald-100 text-emerald-600 hover:bg-emerald-200"
                    }`}
                    title="点击切换加仓/减仓"
                  >
                    {isBuy ? "加仓" : "减仓"}
                  </button>
                  {tx.in_progress ? (
                    <span className="rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-700">
                      交易进行中
                    </span>
                  ) : null}
                  {tx.confirm_date ? (
                    <span className="text-[11px] text-slate-400">确认日 {tx.confirm_date}</span>
                  ) : null}
                </div>

                <div className="space-y-2 pr-8">
                  <input
                    value={tx.fund_name}
                    onChange={(event) => updateAt(index, { fund_name: event.target.value })}
                    className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm font-black text-slate-950 outline-none focus:border-blue-400"
                  />

                  <div className="relative">
                    <div className="flex items-center gap-2">
                      <input
                        value={tx.fund_code ?? ""}
                        onChange={(event) => {
                          const next = event.target.value.replace(/\D/g, "").slice(0, 6);
                          updateAt(index, { fund_code: next || null });
                        }}
                        placeholder="待匹配代码"
                        className={`w-28 rounded-lg border px-2 py-1 text-xs font-bold tabular-nums outline-none focus:border-blue-400 ${
                          unresolved
                            ? "border-amber-300 bg-amber-50 text-amber-800"
                            : "border-slate-200 bg-white text-slate-800"
                        }`}
                      />
                      <button
                        type="button"
                        onClick={() => setSearchIndex(index)}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-semibold text-slate-600 transition hover:border-blue-300 hover:text-blue-700"
                      >
                        <Search size={12} />
                        选择基金
                      </button>
                    </div>
                    {searchIndex === index ? (
                      <FundCodeSearchPanel
                        initialQuery={tx.fund_name}
                        onSelect={(item) => {
                          updateAt(index, {
                            fund_code: item.fund_code,
                            fund_name: item.fund_name,
                          });
                          setSearchIndex(null);
                        }}
                        onClose={() => setSearchIndex(null)}
                      />
                    ) : null}
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <div className="text-[11px] font-semibold text-slate-400">金额（元）</div>
                      <input
                        value={String(tx.amount_yuan ?? 0)}
                        onChange={(event) =>
                          updateAt(index, { amount_yuan: parseAmountInput(event.target.value) })
                        }
                        className="mt-0.5 w-full rounded-lg border border-slate-200 bg-white px-2 py-1 font-black tabular-nums text-slate-950 outline-none focus:border-blue-400"
                      />
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold text-slate-400">成交时间</div>
                      <input
                        value={tx.trade_time}
                        onChange={(event) =>
                          updateAt(index, { trade_time: event.target.value, confirm_date: null })
                        }
                        className="mt-0.5 w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs tabular-nums text-slate-800 outline-none focus:border-blue-400"
                      />
                    </div>
                  </div>
                </div>
              </div>
            );
          })}

          <button
            type="button"
            onClick={onContinueUpload}
            disabled={isBusy}
            className="flex w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-blue-200 bg-blue-50/60 py-3 text-sm font-bold text-blue-600 transition hover:bg-blue-50 disabled:opacity-50"
          >
            <Plus size={15} />
            继续上传
          </button>
        </div>

        <div className="border-t border-slate-100 px-4 py-4">
          {transactions.some((tx) => !tx.fund_code) ? (
            <p className="mb-2 text-center text-[11px] text-amber-600">
              有未匹配代码的交易，确认时将自动跳过。
            </p>
          ) : null}
          <button
            type="button"
            disabled={isBusy || validCount === 0}
            onClick={onConfirm}
            className="w-full rounded-2xl bg-blue-600 px-4 py-3 text-sm font-black text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isBusy ? "正在应用..." : `完成（${validCount}）`}
          </button>
        </div>
      </div>
    </div>
  );
}
