"use client";

import { GitCompareArrows } from "lucide-react";
import type { ReportDiff } from "@/lib/api";

const actionLabel: Record<string, string> = {
  watch: "观察",
  pause_add: "暂停加仓",
  staggered_add: "分批加仓",
  risk_review: "减仓/风控复核",
};

type ReportDiffPanelProps = {
  diff: ReportDiff;
};

export function ReportDiffPanel({ diff }: ReportDiffPanelProps) {
  const hasChanges =
    diff.risk_level_changed ||
    diff.suggested_action_changed ||
    diff.holding_changes.length > 0 ||
    diff.recommendation_changes.length > 0;

  return (
    <section className="rounded-[24px] border border-violet-100 bg-violet-50/60 p-5">
      <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
        <GitCompareArrows size={18} className="text-violet-600" />
        与上一份日报对比
        <span className="text-xs font-semibold text-slate-500">
          {new Date(diff.previous_created_at).toLocaleString("zh-CN")}
        </span>
      </div>

      {!hasChanges ? (
        <p className="text-sm text-slate-600">持仓与建议动作相对上一份无明显变化。</p>
      ) : (
        <div className="space-y-3 text-sm text-slate-700">
          {diff.risk_level_changed || diff.suggested_action_changed ? (
            <div className="rounded-2xl bg-white px-4 py-3">
              <div className="font-bold text-slate-950">组合风险</div>
              <p className="mt-1 leading-6">
                风险等级 {diff.previous_risk_level} → {diff.current_risk_level}；建议动作{" "}
                {actionLabel[diff.previous_suggested_action] ?? diff.previous_suggested_action} →{" "}
                {actionLabel[diff.current_suggested_action] ?? diff.current_suggested_action}；加权收益率变化{" "}
                {diff.weighted_return_delta > 0 ? "+" : ""}
                {diff.weighted_return_delta}%。
              </p>
            </div>
          ) : null}

          {diff.holding_changes.length > 0 ? (
            <div className="rounded-2xl bg-white px-4 py-3">
              <div className="font-bold text-slate-950">持仓变化</div>
              <ul className="mt-2 space-y-2">
                {diff.holding_changes.map((change, index) => (
                  <li key={`${change.fund_code}-${index}`} className="leading-6">
                    {change.type === "added" ? (
                      <>
                        <span className="font-bold text-emerald-700">新增</span> {change.fund_name}（
                        {change.fund_code}）¥{change.holding_amount?.toLocaleString("zh-CN")}
                      </>
                    ) : null}
                    {change.type === "removed" ? (
                      <>
                        <span className="font-bold text-rose-700">移除</span> {change.fund_name}（
                        {change.fund_code}）
                      </>
                    ) : null}
                    {change.type === "changed" ? (
                      <>
                        <span className="font-bold text-blue-700">调整</span> {change.fund_name}：金额
                        {change.holding_amount_delta && change.holding_amount_delta > 0 ? "+" : ""}
                        {change.holding_amount_delta?.toLocaleString("zh-CN")} 元，收益率
                        {change.return_percent_delta && change.return_percent_delta > 0 ? "+" : ""}
                        {change.return_percent_delta}%
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {diff.recommendation_changes.length > 0 ? (
            <div className="rounded-2xl bg-white px-4 py-3">
              <div className="font-bold text-slate-950">操作建议变化</div>
              <ul className="mt-2 space-y-2">
                {diff.recommendation_changes.map((change) => (
                  <li key={change.fund_code} className="leading-6">
                    {change.fund_code}：{change.previous_action ?? "—"} → {change.current_action ?? "—"}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
