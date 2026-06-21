"use client";

import { ArrowRight } from "lucide-react";
import { actionBadgeClass, actionCardClass } from "@/lib/actionStyles";
import type { BriefingDecisions } from "@/lib/todayBriefing";

type BriefingDecisionCardsProps = {
  decisions: BriefingDecisions;
  onViewFullReport: () => void;
};

export function BriefingDecisionCards({ decisions, onViewFullReport }: BriefingDecisionCardsProps) {
  const { portfolioNotes, fundDecisions } = decisions;
  if (!portfolioNotes.length && !fundDecisions.length) {
    return null;
  }

  return (
    <div className="section-card overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3.5">
        <h2 className="section-title">今日决策建议</h2>
        <button
          type="button"
          onClick={onViewFullReport}
          className="inline-flex items-center gap-1 text-xs font-bold text-[var(--brand-strong)] hover:underline"
        >
          完整日报
          <ArrowRight size={13} />
        </button>
      </div>

      {portfolioNotes.length > 0 ? (
        <ul className="space-y-2 border-b border-[var(--line)] px-4 py-3.5">
          {portfolioNotes.map((note, index) => (
            <li
              key={`portfolio-${index}`}
              className="rounded-xl border border-[var(--line)] bg-slate-50/80 px-3.5 py-2.5 text-sm leading-6 text-slate-700"
            >
              {note}
            </li>
          ))}
        </ul>
      ) : null}

      {fundDecisions.length > 0 ? (
        <ul className="divide-y divide-[var(--line)]">
          {fundDecisions.map((item) => (
            <li key={item.fundCode} className={`px-4 py-3.5 ${actionCardClass(item.action)}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-extrabold text-slate-950">{item.fundName}</span>
                <span
                  className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-bold ${actionBadgeClass(item.action)}`}
                >
                  {item.action}
                </span>
              </div>
              {item.point ? (
                <p className="mt-1.5 text-xs leading-5 text-slate-600">{item.point}</p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
