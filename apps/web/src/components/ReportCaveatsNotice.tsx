"use client";

import { ChevronDown } from "lucide-react";

import { translateEvidenceText } from "@/lib/decisionText";

type ReportCaveatsNoticeProps = {
  caveats?: string[];
};

/**
 * `caveats` 里混着两类内容。真正的使用边界（快照时效、证据阻断、当日无要闻、决策窗口、
 * 风格提示）和运行诊断（`分析管线：…`、`板块信号回测：…`）——后者由
 * `_append_pipeline_caveats` 在 `_user_facing_caveats` 过滤**之后**追加，从不经过过滤。
 * 把它们一并挂在「免责声明」标题下会误导用户以为模型名和回测命中率是风险披露，
 * 所以这里分组展示：不丢任何一条，但各自归位。
 */
const DIAGNOSTIC_PREFIXES = ["分析管线：", "板块信号回测："];

function isDiagnosticLine(line: string): boolean {
  return DIAGNOSTIC_PREFIXES.some((prefix) => line.startsWith(prefix));
}

/**
 * 日报的 `caveats` 此前只在流式骨架里短暂出现，成品视图从不渲染——数据时效、
 * 信息缺口这些「这份结论的适用边界」在最终报告里对用户完全不可见。
 */
export function ReportCaveatsNotice({ caveats }: ReportCaveatsNoticeProps) {
  const lines = (caveats ?? []).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) {
    return null;
  }
  const boundaries = lines.filter((line) => !isDiagnosticLine(line));
  const diagnostics = lines.filter(isDiagnosticLine);

  return (
    <details
      className="group rounded-2xl border border-[var(--warn-border)] bg-[var(--warn-bg)]/70"
      data-testid="report-caveats"
    >
      <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-4 text-xs font-black text-[var(--warn-fg)] [&::-webkit-details-marker]:hidden">
        使用边界与免责声明（{boundaries.length} 条）
        <ChevronDown
          size={15}
          aria-hidden="true"
          className="transition group-open:rotate-180"
        />
      </summary>
      <div className="border-t border-[var(--warn-border)] px-4 py-3">
        {boundaries.length ? (
          <div className="space-y-1 text-xs leading-5 text-[var(--warn-fg)]">
            {boundaries.map((line, index) => (
              <p key={`${line}-${index}`} className="break-words [overflow-wrap:anywhere]">
                {translateEvidenceText(line)}
              </p>
            ))}
          </div>
        ) : null}
        {diagnostics.length ? (
          <div
            data-testid="report-caveats-diagnostics"
            className={`space-y-1 text-[11px] leading-5 text-slate-600 ${
              boundaries.length ? "mt-3 border-t border-[var(--warn-border)]/60 pt-2.5" : ""
            }`}
          >
            <p className="font-black text-slate-700">本次运行诊断</p>
            {diagnostics.map((line, index) => (
              <p key={`${line}-${index}`} className="break-words [overflow-wrap:anywhere]">
                {translateEvidenceText(line)}
              </p>
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}
