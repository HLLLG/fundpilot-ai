"use client";

import { BellRing, FolderInput, Timer } from "lucide-react";
import type { AutomationStatus, InboxEvent } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

type AutomationPanelProps = {
  status: AutomationStatus | null;
  events: InboxEvent[];
  autoAnalyzeOnOcr: boolean;
  useAsyncAnalyze: boolean;
  notificationPermission: NotificationPermission | "unsupported";
  onAutoAnalyzeOnOcrChange: (value: boolean) => void;
  onUseAsyncAnalyzeChange: (value: boolean) => void;
  onRequestNotifications: () => void;
  onApplyEvent: (event: InboxEvent) => void;
  onAnalyzeEvent: (event: InboxEvent) => void;
  onDismissEvent: (event: InboxEvent) => void;
};

export function AutomationPanel({
  status,
  events,
  autoAnalyzeOnOcr,
  useAsyncAnalyze,
  notificationPermission,
  onAutoAnalyzeOnOcrChange,
  onUseAsyncAnalyzeChange,
  onRequestNotifications,
  onApplyEvent,
  onAnalyzeEvent,
  onDismissEvent,
}: AutomationPanelProps) {
  return (
    <section className="glass-panel rounded-[28px] p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="mb-2 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-violet-600 text-white">
            <Timer size={20} />
          </div>
          <h2 className="text-xl font-black text-slate-950">自动化（阶段 2）</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
            把养基宝总览截图保存到收件箱文件夹，后端会自动 OCR；浏览器可弹桌面通知。分析任务在后台运行，页面不必一直等待。
          </p>
        </div>
        {status ? (
          <div className="flex flex-wrap gap-2">
            <StatusPill tone={status.inbox_enabled ? "green" : "amber"}>
              收件箱{status.inbox_enabled ? "监听中" : "已关闭"}
            </StatusPill>
            <StatusPill tone={status.schedule_enabled ? "blue" : "dark"}>
              定时 {status.schedule_time}
            </StatusPill>
          </div>
        ) : null}
      </div>

      {status ? (
        <div className="mb-4 rounded-2xl border border-violet-100 bg-violet-50/70 px-4 py-3 text-sm text-slate-700">
          <div className="flex items-start gap-2 font-bold text-slate-950">
            <FolderInput size={16} className="mt-0.5 shrink-0 text-violet-600" />
            收件箱路径（把截图拖进此文件夹）
          </div>
          <code className="mt-2 block break-all text-xs font-semibold text-violet-900">{status.inbox_dir}</code>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            每 {status.inbox_poll_seconds} 秒扫描一次；处理后的文件会移到 <code>processed</code> 子目录。
            {status.schedule_auto_analyze
              ? " 到点若收件箱有待处理持仓，将自动用快速模式提交分析。"
              : " 到点仅推送提醒，不会自动分析（可在 .env 开启 FUND_AI_SCHEDULE_AUTO_ANALYZE）。"}
          </p>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex items-center justify-between rounded-2xl bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm">
          识别完成后自动分析
          <input
            type="checkbox"
            checked={autoAnalyzeOnOcr}
            onChange={(event) => onAutoAnalyzeOnOcrChange(event.target.checked)}
            className="h-5 w-5 accent-violet-600"
          />
        </label>
        <label className="flex items-center justify-between rounded-2xl bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm">
          后台异步分析（推荐）
          <input
            type="checkbox"
            checked={useAsyncAnalyze}
            onChange={(event) => onUseAsyncAnalyzeChange(event.target.checked)}
            className="h-5 w-5 accent-blue-600"
          />
        </label>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onRequestNotifications}
          className="inline-flex items-center gap-2 rounded-full bg-slate-950 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-violet-700"
        >
          <BellRing size={16} />
          开启桌面通知
        </button>
        <span className="text-xs text-slate-500">
          当前权限：
          {notificationPermission === "unsupported"
            ? "浏览器不支持"
            : notificationPermission === "granted"
              ? "已允许"
              : notificationPermission === "denied"
                ? "已拒绝（请在浏览器设置中开启）"
                : "未请求"}
        </span>
      </div>

      {events.length > 0 ? (
        <div className="mt-5 space-y-3">
          <div className="text-sm font-black text-slate-950">待处理事件</div>
          {events.map((event) => (
            <div key={event.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              {event.kind === "schedule_reminder" ? (
                <p className="text-sm leading-6 text-slate-700">{event.payload.message}</p>
              ) : (
                <>
                  <div className="text-sm font-black text-slate-950">
                    收件箱识别：{event.file_name ?? "截图"}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {event.payload.holdings?.length ?? 0} 条持仓
                    {event.payload.error ? ` · ${event.payload.error}` : ""}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => onApplyEvent(event)}
                      className="rounded-full bg-blue-600 px-3 py-1.5 text-xs font-bold text-white"
                    >
                      载入校对
                    </button>
                    <button
                      type="button"
                      onClick={() => onAnalyzeEvent(event)}
                      className="rounded-full bg-violet-600 px-3 py-1.5 text-xs font-bold text-white"
                    >
                      直接分析
                    </button>
                    <button
                      type="button"
                      onClick={() => onDismissEvent(event)}
                      className="rounded-full border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-600"
                    >
                      忽略
                    </button>
                  </div>
                </>
              )}
              {event.kind === "schedule_reminder" ? (
                <button
                  type="button"
                  onClick={() => onDismissEvent(event)}
                  className="mt-3 rounded-full border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-600"
                >
                  知道了
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
