"use client";

import { useState } from "react";
import { ChevronDown, Layers, ShieldAlert } from "lucide-react";
import type { DiscoveryCandidatePoolItem, EliminatedCandidate } from "@/lib/api";
import { translateEvidenceText } from "@/lib/decisionText";

type DiscoveryCandidatePoolPanelProps = {
  pool: DiscoveryCandidatePoolItem[];
  selectedCodes: string[];
  /** M4/M5：被双向 guard 因证据强烈共振剔除的候选（不出现在 recommendations 里）。 */
  eliminatedCandidates?: EliminatedCandidate[];
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
  eliminatedCandidates = [],
}: DiscoveryCandidatePoolPanelProps) {
  const [open, setOpen] = useState(false);
  if (!pool.length) {
    return null;
  }

  const selected = new Set(selectedCodes);
  const eliminatedByCode = new Map(eliminatedCandidates.map((item) => [item.fund_code, item]));

  return (
    <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 w-full items-center justify-between gap-2 px-5 py-4 text-left"
        aria-expanded={open}
        aria-controls="discovery-candidate-pool-content"
      >
        <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
          <Layers size={16} className="text-[var(--brand)]" />
          本次候选池（{pool.length} 只）
          {eliminatedCandidates.length ? (
            <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-bold text-rose-800">
              {eliminatedCandidates.length} 只已被系统剔除
            </span>
          ) : null}
        </div>
        <ChevronDown
          size={18}
          className={`text-slate-500 transition ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open ? (
        <div id="discovery-candidate-pool-content" className="border-t border-slate-100">
          {eliminatedCandidates.length ? (
            <div className="mx-3 mt-3 rounded-xl border border-rose-200 bg-rose-50/80 px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-xs font-black text-rose-900">
                <ShieldAlert size={14} />
                证据强度剔除（量价背离信号显著 + 基金质量分同样偏低）
              </div>
              <ul className="mt-1.5 space-y-1 text-xs leading-5 text-rose-900">
                {eliminatedCandidates.map((item) => (
                  <li key={item.fund_code} className="break-words [overflow-wrap:anywhere]">
                    <span className="font-mono font-semibold">{item.fund_code}</span> {item.fund_name}
                    {item.sector_name ? `（${item.sector_name}）` : ""}：
                    {translateEvidenceText(item.basis || item.reasons.join("；"))}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="grid gap-3 px-3 pb-4 pt-3 lg:hidden">
            {pool.map((item) => {
              const picked = selected.has(item.fund_code);
              const eliminated = eliminatedByCode.get(item.fund_code);
              const reasons =
                compactList(item.quality_reasons) !== "—"
                  ? compactList(item.quality_reasons)
                  : item.selection_reason ?? "—";
              return (
                <article
                  key={`mobile-${item.fund_code}`}
                  className={`rounded-2xl border p-3 ${
                    eliminated
                      ? "border-rose-200 bg-rose-50/70"
                      : picked
                        ? "border-blue-200 bg-[var(--brand-soft)]"
                        : "border-slate-200 bg-white"
                  }`}
                  aria-label={`${item.fund_name}，${eliminated ? "已剔除" : picked ? "已推荐" : "候选"}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className={`break-words text-sm font-black text-slate-900 ${eliminated ? "line-through" : ""}`}>
                        {item.fund_name}
                      </h3>
                      <p className="mt-1 text-xs text-slate-500">
                        <span className="font-mono font-bold">{item.fund_code}</span>
                        {item.sector_label ? ` · ${item.sector_label}` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap justify-end gap-1">
                      {item.is_new_issue ? (
                        <span className="rounded-full bg-amber-100 px-2 py-1 text-[11px] font-bold text-amber-800">新发</span>
                      ) : null}
                      {eliminated || picked ? (
                        <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${
                          eliminated ? "bg-rose-100 text-rose-800" : "bg-blue-100 text-blue-800"
                        }`}>
                          {eliminated ? "已剔除" : "已推荐"}
                        </span>
                      ) : null}
                    </div>
                  </div>

                  <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                    {[
                      ["质量分", formatScore(item.fund_quality_score)],
                      ["匹配分", formatScore(item.sector_fit_score)],
                      ["近3月", formatPercent(item.return_3m_percent)],
                      ["近1年", formatPercent(item.return_1y_percent)],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-xl bg-white/80 px-3 py-2">
                        <dt className="text-slate-500">{label}</dt>
                        <dd className="mt-1 font-black tabular-nums text-slate-900">{value}</dd>
                      </div>
                    ))}
                  </dl>

                  <details className="mt-2 rounded-xl border border-slate-200 bg-white/80">
                    <summary className="flex min-h-11 cursor-pointer items-center px-3 text-xs font-bold text-slate-700">
                      查看质量理由与短板
                    </summary>
                    <div className="border-t border-slate-100 px-3 py-2 text-xs leading-5 text-slate-600">
                      <p><span className="font-bold text-slate-800">理由：</span>{eliminated ? "已被证据强度规则剔除" : reasons}</p>
                      <p className="mt-1 text-amber-800"><span className="font-bold">短板：</span>{compactList(item.quality_penalties)}</p>
                      {item.return_6m_percent != null ? (
                        <p className="mt-1">近6月：{formatPercent(item.return_6m_percent)}</p>
                      ) : null}
                    </div>
                  </details>
                </article>
              );
            })}
          </div>

          <div
            className="hidden overflow-x-auto px-3 pb-4 pt-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-inset lg:block"
            role="region"
            aria-label="基金候选池明细表，可左右滚动查看"
            tabIndex={0}
          >
            <table className="w-full min-w-[920px] text-left text-xs">
              <caption className="sr-only">本次基金候选池评分、收益和质量依据</caption>
              <thead>
                <tr className="text-slate-500">
                  <th scope="col" className="px-2 py-2 font-semibold">代码</th>
                  <th scope="col" className="px-2 py-2 font-semibold">名称</th>
                  <th scope="col" className="px-2 py-2 font-semibold">板块</th>
                  <th scope="col" className="px-2 py-2 font-semibold">质量分</th>
                  <th scope="col" className="px-2 py-2 font-semibold">匹配分</th>
                  <th scope="col" className="px-2 py-2 font-semibold">近3月</th>
                  <th scope="col" className="px-2 py-2 font-semibold">近6月</th>
                  <th scope="col" className="px-2 py-2 font-semibold">近1年</th>
                  <th scope="col" className="px-2 py-2 font-semibold">质量理由</th>
                  <th scope="col" className="px-2 py-2 font-semibold">短板</th>
                </tr>
              </thead>
              <tbody>
                {pool.map((item) => {
                  const picked = selected.has(item.fund_code);
                  const eliminated = eliminatedByCode.get(item.fund_code);
                  return (
                    <tr
                      key={item.fund_code}
                      className={
                        eliminated
                          ? "bg-rose-50/60 text-rose-700"
                          : picked
                            ? "bg-[var(--brand-soft)]"
                            : "border-t border-slate-50"
                      }
                    >
                      <th scope="row" className="px-2 py-2 text-left font-mono font-semibold text-slate-800">
                        {item.fund_code}
                      </th>
                      <td className="max-w-[180px] break-words px-2 py-2 text-slate-700">
                        <span className={eliminated ? "line-through" : ""}>{item.fund_name}</span>
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
                      <td className="max-w-[220px] break-words px-2 py-2 text-slate-600">
                        {eliminated ? (
                          <span className="font-semibold text-rose-700">· 已剔除</span>
                        ) : (
                          <>
                            {compactList(item.quality_reasons) !== "—"
                              ? compactList(item.quality_reasons)
                              : item.selection_reason ?? "—"}
                            {picked ? (
                              <span className="ml-1 font-semibold text-[var(--brand)]">· 已推荐</span>
                            ) : null}
                          </>
                        )}
                      </td>
                      <td className="max-w-[220px] break-words px-2 py-2 text-amber-800">
                        {compactList(item.quality_penalties)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
