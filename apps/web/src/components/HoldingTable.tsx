"use client";

import { Plus, Trash2 } from "lucide-react";
import type { Holding } from "@/lib/api";
import {
  computeEstimatedDailyReturnPercent,
  holdingDailyReturnIsEstimated,
} from "@/lib/holdingMetrics";

function EstimatedDailyReturnCell({ holding }: { holding: Holding }) {
  const estimated = computeEstimatedDailyReturnPercent(holding);
  const isEstimated = holdingDailyReturnIsEstimated(holding);

  if (holding.daily_return_percent != null) {
    return (
      <div
        className="w-32 rounded-xl border border-emerald-100 bg-emerald-50/80 px-3 py-2 text-sm font-medium text-emerald-800"
        title="已填写明确当日收益率，不再使用估算"
      >
        {holding.daily_return_percent.toFixed(2)}%
        <span className="mt-0.5 block text-[10px] font-semibold text-emerald-600">已填当日</span>
      </div>
    );
  }

  if (estimated == null) {
    return (
      <div className="w-32 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-400">
        —
      </div>
    );
  }

  return (
    <div
      className="w-32 rounded-xl border border-amber-100 bg-amber-50/90 px-3 py-2 text-sm font-semibold text-amber-900"
      title="估算当日收益率 ≈ 板块涨跌 + 持有收益率（昨日结算）"
    >
      {isEstimated ? `≈${estimated.toFixed(2)}%` : `${estimated.toFixed(2)}%`}
      {isEstimated ? (
        <span className="mt-0.5 block text-[10px] font-semibold text-amber-700">板块+持有</span>
      ) : null}
    </div>
  );
}

type HoldingTableProps = {
  holdings: Holding[];
  onChange: (holdings: Holding[]) => void;
};

export function HoldingTable({ holdings, onChange }: HoldingTableProps) {
  const updateHolding = (index: number, patch: Partial<Holding>) => {
    onChange(
      holdings.map((holding, itemIndex) =>
        itemIndex === index ? { ...holding, ...patch } : holding,
      ),
    );
  };

  const addHolding = () => {
    onChange([
      ...holdings,
        {
          fund_code: "000000",
          fund_name: "新基金",
          holding_amount: 0,
          return_percent: 0,
          daily_profit: null,
          daily_return_percent: null,
          holding_profit: null,
          holding_return_percent: null,
          sector_name: "",
          sector_return_percent: null,
        },
    ]);
  };

  const removeHolding = (index: number) => {
    onChange(holdings.filter((_, itemIndex) => itemIndex !== index));
  };

  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6">
      <div className="mb-5 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-slate-950">持仓校对</h2>
          <p className="mt-1 text-sm text-slate-500">
            OCR 只负责草稿，最终以你确认的表格为准。无「当日收益率」时，估算列 = 板块涨跌 + 持有收益率。
          </p>
        </div>
        <button
          type="button"
          onClick={addHolding}
          className="inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full bg-slate-950 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700"
        >
          <Plus size={16} />
          新增
        </button>
      </div>

      <div className="max-w-full overflow-x-auto">
        <table className="w-full min-w-[1420px] border-separate border-spacing-y-3">
          <thead>
            <tr className="text-left text-xs font-bold uppercase text-slate-400">
              <th className="px-3">基金代码</th>
              <th className="px-3">基金名称</th>
              <th className="px-3">持有金额</th>
              <th className="px-3">当日收益额</th>
              <th className="px-3">当日收益率</th>
              <th className="px-3">关联板块</th>
              <th className="px-3">板块涨跌</th>
              <th className="px-3">持有收益额</th>
              <th className="px-3">持有收益率</th>
              <th className="px-3" title="无当日收益率时 ≈ 板块涨跌 + 持有收益率">
                估算当日收益率
              </th>
              <th className="px-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((holding, index) => (
              <tr key={`${holding.fund_code}-${index}`} className="bg-white shadow-sm">
                <td className="rounded-l-2xl px-3 py-3">
                  <input
                    value={holding.fund_code}
                    onChange={(event) => updateHolding(index, { fund_code: event.target.value })}
                    className="w-24 rounded-xl border border-slate-200 px-3 py-2 text-sm font-bold outline-none focus:border-blue-400"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.fund_name}
                    onChange={(event) => updateHolding(index, { fund_name: event.target.value })}
                    className="w-full min-w-52 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.holding_amount}
                    type="number"
                    min={0}
                    step="0.01"
                    onChange={(event) =>
                      updateHolding(index, { holding_amount: Number(event.target.value) })
                    }
                    className="w-32 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.daily_profit ?? ""}
                    type="number"
                    step="0.01"
                    onChange={(event) =>
                      updateHolding(index, {
                        daily_profit:
                          event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                    className="w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                    placeholder="如 -86.23"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.daily_return_percent ?? ""}
                    type="number"
                    step="0.01"
                    onChange={(event) =>
                      updateHolding(index, {
                        daily_return_percent:
                          event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                    className="w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                    placeholder="如 -0.57"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.sector_name ?? ""}
                    onChange={(event) => updateHolding(index, { sector_name: event.target.value })}
                    className="w-36 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                    placeholder="如 半导体"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.sector_return_percent ?? ""}
                    type="number"
                    step="0.01"
                    onChange={(event) =>
                      updateHolding(index, {
                        sector_return_percent:
                          event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                    className="w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                    placeholder="如 3.33"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.holding_profit ?? ""}
                    type="number"
                    step="0.01"
                    onChange={(event) =>
                      updateHolding(index, {
                        holding_profit:
                          event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                    className="w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                    placeholder="如 401.80"
                  />
                </td>
                <td className="px-3 py-3">
                  <input
                    value={holding.holding_return_percent ?? holding.return_percent}
                    type="number"
                    step="0.01"
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      updateHolding(index, {
                        holding_return_percent: value,
                        return_percent: value,
                      });
                    }}
                    className="w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                    placeholder="如 2.74"
                  />
                </td>
                <td className="px-3 py-3">
                  <EstimatedDailyReturnCell holding={holding} />
                </td>
                <td className="rounded-r-2xl px-3 py-3 text-right">
                  <button
                    type="button"
                    onClick={() => removeHolding(index)}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                    aria-label="删除持仓"
                  >
                    <Trash2 size={18} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {holdings.length === 0 ? (
        <div className="rounded-3xl border border-dashed border-slate-200 bg-white px-5 py-8 text-center text-sm text-slate-500">
          暂无持仓。上传截图、粘贴 OCR 文本，或手动新增一行。
        </div>
      ) : null}
    </section>
  );
}
