"use client";

import { useEffect, useState } from "react";
import { Search, X } from "lucide-react";
import type { FundSearchItem, Holding } from "@/lib/api";
import { searchFunds } from "@/lib/api";
import { cnProfitClass } from "@/lib/holdingMetrics";

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
  ocrSource?: string | null;
  isBusy?: boolean;
  onChange: (holdings: Holding[]) => void;
  onConfirm: () => void;
  onClose: () => void;
};

function parseAmountInput(value: string): number {
  const parsed = Number.parseFloat(value.replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseProfitInput(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number.parseFloat(trimmed.replace(/,/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function displayCode(holding: Holding, resolution?: FundCodeResolution) {
  if (holding.fund_code && holding.fund_code !== "000000") {
    return holding.fund_code;
  }
  return resolution?.fund_code ?? "";
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
  const [searchIndex, setSearchIndex] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const removeAt = (index: number) => {
    onChange(holdings.filter((_, itemIndex) => itemIndex !== index));
  };

  const updateAt = (index: number, patch: Partial<Holding>) => {
    onChange(holdings.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  };

  const openSearch = (index: number) => {
    setSearchIndex(index);
    setSearchQuery(holdings[index]?.fund_name ?? "");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-4 sm:items-center">
      <div className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-[28px] bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-lg font-black text-slate-950">
              确认识别结果
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              可修改基金代码、名称、金额与收益；代码不对时点搜索从东财选取。
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
            const code = displayCode(holding, resolution);
            const unresolved = !code;

            return (
              <div
                key={`${holding.fund_name}-${index}`}
                className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3"
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="relative">
                      <div className="flex items-center gap-2">
                        <input
                          value={code}
                          onChange={(event) => {
                            const next = event.target.value.replace(/\D/g, "").slice(0, 6);
                            updateAt(index, { fund_code: next || "000000" });
                          }}
                          placeholder="待匹配"
                          className={`w-24 rounded-lg border px-2 py-1 text-xs font-bold tabular-nums outline-none focus:border-blue-400 ${
                            unresolved
                              ? "border-amber-300 bg-amber-50 text-amber-800"
                              : "border-slate-200 bg-white text-slate-800"
                          }`}
                        />
                        <button
                          type="button"
                          onClick={() => openSearch(index)}
                          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-semibold text-slate-600 transition hover:border-blue-300 hover:text-blue-700"
                        >
                          <Search size={12} />
                          搜索
                        </button>
                        {resolution?.source ? (
                          <span className="text-[10px] text-slate-400">{resolution.source}</span>
                        ) : null}
                      </div>
                      {searchIndex === index ? (
                        <FundCodeSearchPanel
                          initialQuery={searchQuery}
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
                    <input
                      value={holding.fund_name}
                      onChange={(event) => updateAt(index, { fund_name: event.target.value })}
                      className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm font-black text-slate-950 outline-none focus:border-blue-400"
                    />
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
                    <input
                      value={String(holding.holding_amount ?? 0)}
                      onChange={(event) =>
                        updateAt(index, { holding_amount: parseAmountInput(event.target.value) })
                      }
                      className="mt-0.5 w-full rounded-lg border border-slate-200 bg-white px-2 py-1 font-black tabular-nums text-slate-950 outline-none focus:border-blue-400"
                    />
                  </div>
                  <div>
                    <div className="text-[11px] font-semibold text-slate-400">持有收益</div>
                    <input
                      value={
                        holding.holding_profit === null || holding.holding_profit === undefined
                          ? ""
                          : String(holding.holding_profit)
                      }
                      onChange={(event) =>
                        updateAt(index, { holding_profit: parseProfitInput(event.target.value) })
                      }
                      className={`mt-0.5 w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-right font-black tabular-nums outline-none focus:border-blue-400 ${cnProfitClass(holding.holding_profit)}`}
                    />
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
