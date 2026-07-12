"use client";

import { useState } from "react";
import { CheckCircle2, Circle, Loader2, Send } from "lucide-react";
import type { StreamingReportState } from "@/lib/streamApi";
import { PRE_LLM_FOLLOWUP_STAGES } from "@/lib/streamApi";
import {
  ANALYSIS_STAGE_ORDER,
  stageCardStatus,
  stageShortLabel,
} from "@/lib/streamingStageMeta";

type ReportThinkingSidebarProps = {
  streaming: StreamingReportState;
  onFollowup?: (message: string) => Promise<void>;
};

function elapsedSeconds(startedAt: number): string {
  const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
  return `${seconds}s`;
}

export function ReportThinkingSidebar({ streaming, onFollowup }: ReportThinkingSidebarProps) {
  const [followupDraft, setFollowupDraft] = useState("");
  const [followupError, setFollowupError] = useState<string | null>(null);
  const [followupSending, setFollowupSending] = useState(false);
  const completedStages = new Set(streaming.stageLog.map((entry) => entry.stage));
  const canFollowup =
    Boolean(streaming.sessionId) &&
    PRE_LLM_FOLLOWUP_STAGES.has(streaming.stage) &&
    Boolean(onFollowup);

  const handleFollowupSubmit = async () => {
    const text = followupDraft.trim();
    if (!text || !onFollowup) {
      return;
    }
    setFollowupSending(true);
    setFollowupError(null);
    try {
      await onFollowup(text);
      setFollowupDraft("");
    } catch (error) {
      setFollowupError(error instanceof Error ? error.message : "发送失败");
    } finally {
      setFollowupSending(false);
    }
  };

  return (
    <aside
      className="rounded-2xl border border-slate-200 bg-slate-50/90 p-4"
      data-testid="report-thinking-sidebar"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-xs font-black uppercase tracking-wide text-slate-500">分析过程</h3>
        <span className="text-xs font-bold text-slate-500">{elapsedSeconds(streaming.startedAt)}</span>
      </div>

      <ol className="space-y-2">
        {ANALYSIS_STAGE_ORDER.map((stageId) => {
          const status = stageCardStatus(stageId, streaming.stage, completedStages);
          const logEntry = streaming.stageLog.find((entry) => entry.stage === stageId);
          return (
            <li
              key={stageId}
              className={`flex items-start gap-2 rounded-xl px-2 py-1.5 text-sm ${
                status === "active" ? "bg-white shadow-sm" : ""
              }`}
              data-testid={`stage-card-${stageId}`}
              data-status={status}
            >
              {status === "done" ? (
                <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-700" />
              ) : status === "active" ? (
                <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin text-[var(--brand-strong)]" />
              ) : (
                <Circle size={16} className="mt-0.5 shrink-0 text-slate-300" />
              )}
              <div className="min-w-0">
                <div
                  className={`font-bold ${
                    status === "pending" ? "text-slate-500" : "text-slate-800"
                  }`}
                >
                  {stageShortLabel(stageId)}
                </div>
                {logEntry && status !== "pending" ? (
                  <div className="mt-0.5 text-xs leading-5 text-slate-500">{logEntry.label}</div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      {streaming.thinkingNotes.length > 0 ? (
        <div className="mt-4 border-t border-slate-200 pt-3">
          <h4 className="text-xs font-black text-slate-500">输出摘要</h4>
          <ul className="mt-2 max-h-40 space-y-1.5 overflow-y-auto text-xs leading-5 text-slate-600">
            {streaming.thinkingNotes.map((note, index) => (
              <li key={`${note}-${index}`} className="flex gap-1.5">
                <span className="text-slate-300">·</span>
                <span>{note}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {canFollowup ? (
        <div className="mt-4 border-t border-slate-200 pt-3" data-testid="stream-followup-box">
          <h4 className="text-xs font-black text-slate-500">补充说明</h4>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            在 AI 开始写报告前，可追加关注方向或约束（进入「AI 分析」后不可修改）。
          </p>
          {streaming.followupNotes.length > 0 ? (
            <ul className="mt-2 space-y-1 text-xs text-slate-600">
              {streaming.followupNotes.map((note, index) => (
                <li key={`${note}-${index}`} className="rounded-lg bg-white px-2 py-1">
                  {note}
                </li>
              ))}
            </ul>
          ) : null}
          <textarea
            aria-label="补充分析要求"
            value={followupDraft}
            onChange={(event) => setFollowupDraft(event.target.value)}
            rows={2}
            placeholder="例如：请重点分析半导体与电网设备…"
            className="mt-2 w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs outline-none focus:border-[var(--brand)]"
          />
          {followupError ? (
            <p role="alert" className="mt-1 text-[11px] text-red-600">
              {followupError}
            </p>
          ) : null}
          <button
            type="button"
            disabled={followupSending || !followupDraft.trim()}
            onClick={() => void handleFollowupSubmit()}
            className="mt-2 inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-[11px] font-bold text-white disabled:opacity-50"
          >
            {followupSending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
            发送补充
          </button>
        </div>
      ) : null}

      {streaming.stage === "generating" && streaming.tokenBuffer ? (
        <div className="mt-4 border-t border-slate-200 pt-3" data-testid="stream-token-preview">
          <h4 className="text-xs font-black text-slate-500">模型输出（流式预览）</h4>
          <pre className="mt-2 max-h-32 overflow-hidden whitespace-pre-wrap break-all rounded-xl bg-slate-900/90 p-3 font-mono text-[11px] leading-5 text-slate-100">
            {streaming.tokenBuffer.slice(-320)}
            <span className="animate-pulse text-emerald-400">▍</span>
          </pre>
        </div>
      ) : null}
    </aside>
  );
}
