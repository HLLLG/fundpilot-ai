"use client";

import { useState } from "react";
import { ChevronDown, Layers } from "lucide-react";
import type { DiscoveryCandidatePoolItem } from "@/lib/api";

type DiscoveryCandidatePoolPanelProps = {
  pool: DiscoveryCandidatePoolItem[];
  selectedCodes: string[];
};

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return `${value}%`;
}

function formatScore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return Number(value).toFixed(2).replace(/\.00$/, "");
}

function compactList(items: string[] | undefined): string {
  if (!items?.length) {
    return "—";
  }
  return items.slice(0, 2).join("；");
}

export function DiscoveryCandidatePoolPanel({
  pool,
  selectedCodes,
}: DiscoveryCandidatePoolPanelProps) {
  const [open, setOpen] = useState(false);
  if (!pool.length) {
    return null;
  }

  const selected = new Set(selectedCodes);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-2 px-5 py-4 text-left"
      >
        <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
          <Layers size={16} className="text-[var(--brand)]" />
          本次候选池（{pool.length} 只）
        </div>
        <ChevronDown
          size={18}
          className={`text-slate-400 transition ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open ? (
        <div className="overflow-x-auto border-t border-slate-100 px-3 pb-4">
          <table className="w-full min-w-[920px] text-left text-xs">
            <thead>
              <tr className="text-slate-500">
                <th className="px-2 py-2 font-semibold">代码</th>
                <th className="px-2 py-2 font-semibold">名称</th>
                <th className="px-2 py-2 font-semibold">板块</th>
                <th className="px-2 py-2 font-semibold">质量分</th>
                <th className="px-2 py-2 font-semibold">匹配分</th>
                <th className="px-2 py-2 font-semibold">近3月</th>
                <th className="px-2 py-2 font-semibold">近6月</th>
                <th className="px-2 py-2 font-semibold">近1年</th>
                <th className="px-2 py-2 font-semibold">质量理由</th>
                <th className="px-2 py-2 font-semibold">短板</th>
              </tr>
            </thead>
            <tbody>
              {pool.map((item) => {
                const picked = selected.has(item.fund_code);
                return (
                  <tr
                    key={item.fund_code}
                    className={picked ? "bg-[var(--brand-soft)]" : "border-t border-slate-50"}
                  >
                    <td className="px-2 py-2 font-mono font-semibold text-slate-800">
                      {item.fund_code}
                    </td>
                    <td className="max-w-[140px] truncate px-2 py-2 text-slate-700">
                      {item.fund_name}
                      {item.is_new_issue ? (
                        <span className="ml-1 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-bold text-amber-800">
                          新发
                        </span>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 text-slate-600">{item.sector_label ?? "—"}</td>
                    <td className="px-2 py-2 font-semibold text-slate-800">
                      {formatScore(item.fund_quality_score)}
                    </td>
                    <td className="px-2 py-2 font-semibold text-slate-700">
                      {formatScore(item.sector_fit_score)}
                    </td>
                    <td className="px-2 py-2 text-slate-600">
                      {formatPercent(item.return_3m_percent)}
                    </td>
                    <td className="px-2 py-2 text-slate-600">
                      {formatPercent(item.return_6m_percent)}
                    </td>
                    <td className="px-2 py-2 text-slate-600">
                      {formatPercent(item.return_1y_percent)}
                    </td>
                    <td className="max-w-[220px] px-2 py-2 text-slate-600">
                      {compactList(item.quality_reasons) !== "—"
                        ? compactList(item.quality_reasons)
                        : item.selection_reason ?? "—"}
                      {picked ? (
                        <span className="ml-1 font-semibold text-[var(--brand)]">· 已推荐</span>
                      ) : null}
                    </td>
                    <td className="max-w-[220px] px-2 py-2 text-amber-800">
                      {compactList(item.quality_penalties)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
