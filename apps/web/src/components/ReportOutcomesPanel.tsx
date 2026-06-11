"use client";

import { useEffect, useState } from "react";
import { CalendarRange, History } from "lucide-react";
import {
  fetchReportOutcomes,
  fetchReportWeeklyOutcomes,
  type ReportOutcomes,
  type ReportWeeklyOutcomes,
} from "@/lib/api";

type ReportOutcomesPanelProps = {
  reportId: string;
  embedded?: boolean;
};

function OutcomeItems({ outcomes }: { outcomes: ReportOutcomes }) {
  if (!outcomes.has_baseline) {
    return <p className="text-sm text-slate-600">{outcomes.message}</p>;
  }

  return (
    <>
      {outcomes.portfolio_trend_summary ? (
        <p className="mb-3 text-sm font-semibold text-slate-700">{outcomes.portfolio_trend_summary}</p>
      ) : null}
      {outcomes.portfolio_return_delta !== null && outcomes.portfolio_return_delta !== undefined ? (
        <p className="mb-3 text-sm font-semibold text-slate-700">
          组合加权收益率变化：{outcomes.portfolio_return_delta > 0 ? "+" : ""}
          {outcomes.portfolio_return_delta}%
          {outcomes.portfolio_assets_delta_percent !== null &&
          outcomes.portfolio_assets_delta_percent !== undefined
            ? ` · 近一周资产约 ${outcomes.portfolio_assets_delta_percent > 0 ? "+" : ""}${outcomes.portfolio_assets_delta_percent}%`
            : ""}
        </p>
      ) : null}
      <div className="space-y-2">
        {outcomes.items.map((item) => (
          <div key={item.fund_code} className="rounded-2xl bg-white px-4 py-3 text-sm text-slate-700">
            <div className="font-black text-slate-950">
              {item.fund_name}（{item.fund_code}）
            </div>
            <div className="mt-1 text-xs text-slate-500">
              上一份建议：{item.previous_action} → 本次：{item.current_action}
              {item.daily_return_delta !== null && item.daily_return_delta !== undefined
                ? ` · 当日涨跌变化 ${item.daily_return_delta > 0 ? "+" : ""}${item.daily_return_delta}%`
                : item.holding_return_delta !== null && item.holding_return_delta !== undefined
                  ? ` · 持有收益变化 ${item.holding_return_delta > 0 ? "+" : ""}${item.holding_return_delta}%`
                  : ""}
            </div>
            <div className="mt-2 text-xs leading-5 text-violet-900">{item.assessment}</div>
          </div>
        ))}
      </div>
    </>
  );
}

export function ReportOutcomesPanel({ reportId, embedded = false }: ReportOutcomesPanelProps) {
  const [outcomes, setOutcomes] = useState<ReportOutcomes | null>(null);
  const [weekly, setWeekly] = useState<ReportWeeklyOutcomes | null>(null);

  useEffect(() => {
    void fetchReportOutcomes(reportId)
      .then(setOutcomes)
      .catch(() => setOutcomes(null));
    void fetchReportWeeklyOutcomes(reportId)
      .then(setWeekly)
      .catch(() => setWeekly(null));
  }, [reportId]);

  if (!outcomes && !weekly) {
    return null;
  }

  const body = (
    <div className="space-y-5">
      {outcomes ? <OutcomeItems outcomes={outcomes} /> : null}
      {weekly?.reversal_stats?.summary_line ? (
        <div className="rounded-2xl border border-amber-100 bg-amber-50/60 p-4 text-sm text-amber-950">
          <div className="font-black">涨后回吐复盘</div>
          <p className="mt-1 leading-6">{weekly.reversal_stats.summary_line}</p>
        </div>
      ) : null}
      {weekly ? (
        <div className="rounded-2xl border border-indigo-100 bg-indigo-50/50 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-black text-slate-950">
            <CalendarRange size={16} className="text-indigo-600" />
            7 日建议复盘
          </div>
          {!weekly.has_baseline ? (
            <p className="text-sm text-slate-600">{weekly.message}</p>
          ) : (
            <>
              {weekly.summary ? (
                <p className="mb-3 text-sm font-semibold text-indigo-950">{weekly.summary}</p>
              ) : null}
              <OutcomeItems outcomes={weekly} />
            </>
          )}
        </div>
      ) : null}
    </div>
  );

  if (embedded) {
    return body;
  }

  return (
    <div className="mb-5 rounded-[24px] border border-violet-100 bg-violet-50/60 p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
        <History size={18} className="text-violet-600" />
        建议复盘
      </div>
      {body}
    </div>
  );
}
