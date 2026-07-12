"use client";

import { useEffect, useState } from "react";
import { CalendarRange, History } from "lucide-react";
import {
  fetchReportOutcomes,
  fetchReportWeeklyOutcomes,
  type ReportOutcomes,
  type ReportWeeklyOutcomes,
} from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";

type ReportOutcomesPanelProps = {
  reportId: string;
  embedded?: boolean;
};

function OutcomeItems({ outcomes }: { outcomes: ReportOutcomes }) {
  if (!outcomes.has_baseline) {
    return (
      <InlineNotice
        tone="info"
        message={outcomes.message ?? "缺少可对比的上一份报告，暂无法生成复盘。"}
      />
    );
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
            <div className="mt-2 text-xs leading-5 text-slate-600">{item.assessment}</div>
          </div>
        ))}
        {outcomes.items.length === 0 ? (
          <InlineNotice tone="info" message="已有对比基准，但暂无基金级复盘明细。" />
        ) : null}
      </div>
    </>
  );
}

export function ReportOutcomesPanel({ reportId, embedded = false }: ReportOutcomesPanelProps) {
  const [outcomesResult, setOutcomesResult] = useState<{
    reportId: string;
    data: ReportOutcomes;
  } | null>(null);
  const [weeklyResult, setWeeklyResult] = useState<{
    reportId: string;
    data: ReportWeeklyOutcomes;
  } | null>(null);
  const [outcomesLoading, setOutcomesLoading] = useState(true);
  const [weeklyLoading, setWeeklyLoading] = useState(true);
  const [outcomesErrorResult, setOutcomesErrorResult] = useState<{
    reportId: string;
    message: string;
  } | null>(null);
  const [weeklyErrorResult, setWeeklyErrorResult] = useState<{
    reportId: string;
    message: string;
  } | null>(null);
  const [outcomesRetrySequence, setOutcomesRetrySequence] = useState(0);
  const [weeklyRetrySequence, setWeeklyRetrySequence] = useState(0);
  const outcomes = outcomesResult?.reportId === reportId ? outcomesResult.data : null;
  const weekly = weeklyResult?.reportId === reportId ? weeklyResult.data : null;
  const outcomesError =
    outcomesErrorResult?.reportId === reportId ? outcomesErrorResult.message : null;
  const weeklyError = weeklyErrorResult?.reportId === reportId ? weeklyErrorResult.message : null;

  useEffect(() => {
    let cancelled = false;
    setOutcomesLoading(true);
    setOutcomesErrorResult(null);
    void fetchReportOutcomes(reportId)
      .then((data) => {
        if (!cancelled) {
          setOutcomesResult({ reportId, data });
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setOutcomesErrorResult({
            reportId,
            message: loadError instanceof Error ? loadError.message : "当期复盘加载失败",
          });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setOutcomesLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [outcomesRetrySequence, reportId]);

  useEffect(() => {
    let cancelled = false;
    setWeeklyLoading(true);
    setWeeklyErrorResult(null);
    void fetchReportWeeklyOutcomes(reportId)
      .then((data) => {
        if (!cancelled) {
          setWeeklyResult({ reportId, data });
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setWeeklyErrorResult({
            reportId,
            message: loadError instanceof Error ? loadError.message : "7 日复盘加载失败",
          });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setWeeklyLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reportId, weeklyRetrySequence]);

  const outcomesPending = outcomesLoading || (!outcomes && !outcomesError);
  const weeklyPending = weeklyLoading || (!weekly && !weeklyError);
  const isLoading = outcomesPending || weeklyPending;
  const initialLoading =
    outcomesPending && weeklyPending && !outcomes && !weekly && !outcomesError && !weeklyError;
  const hasAnyResult = Boolean(outcomes || weekly);

  const body = (
    <div className="space-y-5" aria-busy={isLoading}>
      {initialLoading ? (
        <InlineNotice tone="info" message="正在加载当期与 7 日建议复盘…" />
      ) : null}
      {!initialLoading && outcomesPending ? (
        <InlineNotice
          tone="info"
          message={outcomes ? "正在更新当期复盘，当前继续显示已有结果。" : "正在加载当期复盘…"}
        />
      ) : null}
      {!initialLoading && weeklyPending ? (
        <InlineNotice
          tone="info"
          message={weekly ? "正在更新 7 日复盘，当前继续显示已有结果。" : "正在加载 7 日复盘…"}
        />
      ) : null}
      {outcomesError ? (
        <InlineNotice
          tone={hasAnyResult ? "warning" : "error"}
          message={
            outcomes
              ? `当期复盘更新失败，继续显示上次成功获取的结果：${outcomesError}`
              : weekly
                ? `当期复盘加载失败，7 日结果仍可查看：${outcomesError}`
                : `当期复盘加载失败：${outcomesError}`
          }
          action={{
            label: "重试当期",
            onClick: () => setOutcomesRetrySequence((current) => current + 1),
          }}
        />
      ) : null}
      {weeklyError ? (
        <InlineNotice
          tone={hasAnyResult ? "warning" : "error"}
          message={
            weekly
              ? `7 日复盘更新失败，继续显示上次成功获取的结果：${weeklyError}`
              : outcomes
                ? `7 日复盘加载失败，当期结果仍可查看：${weeklyError}`
                : `7 日复盘加载失败：${weeklyError}`
          }
          action={{
            label: "重试 7 日",
            onClick: () => setWeeklyRetrySequence((current) => current + 1),
          }}
        />
      ) : null}
      {outcomes ? <OutcomeItems outcomes={outcomes} /> : null}
      {weekly?.reversal_stats?.summary_line ? (
        <div className="rounded-2xl border border-amber-100 bg-amber-50/60 p-4 text-sm text-amber-950">
          <div className="font-black">涨后回吐复盘</div>
          <p className="mt-1 leading-6">{weekly.reversal_stats.summary_line}</p>
        </div>
      ) : null}
      {weekly ? (
        <div className="rounded-2xl border border-[rgba(37,99,235,0.18)] bg-[var(--brand-soft)] p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-black text-slate-950">
            <CalendarRange size={16} className="text-[var(--brand)]" />
            7 日建议复盘
          </div>
          {!weekly.has_baseline ? (
            <p className="text-sm text-slate-600">{weekly.message}</p>
          ) : (
            <>
              {weekly.summary ? (
                <p className="mb-3 text-sm font-semibold text-[var(--brand-strong)]">{weekly.summary}</p>
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
    <div className="mb-5 rounded-[24px] border border-[rgba(37,99,235,0.18)] bg-[var(--brand-soft)] p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
        <History size={18} className="text-[var(--brand)]" />
        建议复盘
      </div>
      {body}
    </div>
  );
}
