"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Ban,
  Check,
  CheckCircle2,
  Clipboard,
  Loader2,
  RotateCcw,
  X,
} from "lucide-react";
import {
  fetchOpsErrorGroup,
  updateOpsErrorStatus,
  type OpsErrorEvent,
  type OpsErrorGroup,
  type OpsErrorGroupDetail,
  type OpsErrorStatus,
} from "@/lib/api/ops";
import { userFacingErrorMessage } from "@/lib/userFacingError";

const TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "暂无";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : TIME_FORMATTER.format(date);
}

const SOURCE_LABELS: Record<string, string> = {
  frontend: "前端",
  backend: "后端",
  worker: "后台任务",
};

const KIND_LABELS: Record<string, string> = {
  window_error: "未捕获异常",
  unhandled_rejection: "未处理的 Promise",
  react_render: "渲染崩溃",
  resource_load: "资源加载失败",
  api_failure: "接口调用失败",
  manual: "主动上报",
};

type OpsErrorDetailPanelProps = {
  fingerprint: string;
  onClose: () => void;
  onStatusChange: (group: OpsErrorGroup) => void;
};

export function OpsErrorDetailPanel({
  fingerprint,
  onClose,
  onStatusChange,
}: OpsErrorDetailPanelProps) {
  const [detail, setDetail] = useState<OpsErrorGroupDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchOpsErrorGroup(fingerprint);
      setDetail(next);
      setNote(next.group.note ?? "");
      setSelectedEventId(next.events[0]?.eventId ?? null);
    } catch (nextError) {
      setError(userFacingErrorMessage(nextError, "无法读取错误详情"));
    } finally {
      setLoading(false);
    }
  }, [fingerprint]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const selectedEvent: OpsErrorEvent | null = useMemo(() => {
    if (!detail) {
      return null;
    }
    return (
      detail.events.find((event) => event.eventId === selectedEventId) ??
      detail.events[0] ??
      null
    );
  }, [detail, selectedEventId]);

  const applyStatus = async (status: OpsErrorStatus) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateOpsErrorStatus(fingerprint, { status, note });
      setDetail((current) => (current ? { ...current, group: updated } : current));
      onStatusChange(updated);
    } catch (nextError) {
      setError(userFacingErrorMessage(nextError, "更新状态失败"));
    } finally {
      setSaving(false);
    }
  };

  const copyStack = async () => {
    if (!selectedEvent?.stack) {
      return;
    }
    try {
      await navigator.clipboard.writeText(selectedEvent.stack);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const group = detail?.group;
  const maxHourly = Math.max(1, ...(detail?.hourly ?? []).map((item) => item.eventCount));

  return (
    <div className="fixed inset-0 z-[70] flex justify-end">
      <button
        type="button"
        aria-label="关闭详情"
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/30"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-label="错误详情"
        className="relative flex h-full w-full max-w-3xl flex-col overflow-hidden border-l border-slate-200 bg-white shadow-[0_0_60px_rgba(15,23,42,0.2)]"
      >
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-600">
                {SOURCE_LABELS[group?.source ?? ""] ?? group?.source ?? "—"}
              </span>
              {group?.status === "resolved" ? (
                <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-bold text-emerald-700">
                  已解决
                </span>
              ) : group?.status === "ignored" ? (
                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-500">
                  已忽略
                </span>
              ) : (
                <span className="rounded-md bg-red-50 px-2 py-0.5 text-xs font-bold text-red-700">
                  未解决
                </span>
              )}
              <code className="font-mono text-xs text-slate-500">{fingerprint}</code>
            </div>
            <h2 className="mt-1.5 truncate text-base font-bold text-slate-900">
              {group?.errorType ?? "加载中"}
            </h2>
            <p className="mt-0.5 line-clamp-2 text-sm text-slate-600">{group?.message}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="shrink-0 rounded-lg p-2 text-slate-500 transition hover:bg-slate-100"
          >
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <p className="flex items-center gap-2 py-10 text-sm text-slate-500">
              <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              正在加载错误详情…
            </p>
          ) : null}

          {error ? (
            <p className="mb-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          ) : null}

          {detail && group ? (
            <>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { label: "累计次数", value: String(group.eventCount) },
                  { label: "影响用户", value: String(group.affectedUserCount) },
                  { label: "首次出现", value: formatTime(group.firstSeenAt) },
                  { label: "最近出现", value: formatTime(group.lastSeenAt) },
                ].map((item) => (
                  <div
                    key={item.label}
                    className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2"
                  >
                    <dt className="text-xs font-bold text-slate-500">{item.label}</dt>
                    <dd className="mt-0.5 text-sm font-bold text-slate-800">
                      {item.value}
                    </dd>
                  </div>
                ))}
              </dl>

              {group.eventCount > detail.storedEventCount ? (
                <p className="mt-2 text-xs text-slate-500">
                  共发生 {group.eventCount} 次，为控制存储量按分钟采样保留了{" "}
                  {detail.storedEventCount} 条明细。
                </p>
              ) : null}

              {detail.hourly.length > 0 ? (
                <section className="mt-5">
                  <h3 className="text-sm font-bold text-slate-800">发生频次（按小时）</h3>
                  <div
                    className="mt-2 flex h-16 items-end gap-0.5"
                    role="img"
                    aria-label={`按小时发生频次，最高 ${maxHourly} 次`}
                  >
                    {detail.hourly.map((item) => (
                      <span
                        key={item.hour}
                        title={`${item.hour} — ${item.eventCount} 次`}
                        className="min-w-0 flex-1 rounded-sm bg-red-500/70"
                        style={{
                          height: `${Math.max(6, (item.eventCount / maxHourly) * 100)}%`,
                        }}
                      />
                    ))}
                  </div>
                </section>
              ) : null}

              <section className="mt-5">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-bold text-slate-800">最近发生记录</h3>
                  <span className="text-xs text-slate-500">
                    共 {detail.events.length} 条，点击查看对应堆栈
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {detail.events.map((event) => {
                    const active = event.eventId === selectedEvent?.eventId;
                    return (
                      <button
                        key={event.eventId}
                        type="button"
                        onClick={() => setSelectedEventId(event.eventId)}
                        aria-pressed={active}
                        className={`rounded-lg border px-2.5 py-1.5 font-mono text-xs transition ${
                          active
                            ? "border-[var(--brand)] bg-[var(--info-bg)] text-[var(--brand-strong)]"
                            : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        {formatTime(event.occurredAt)}
                      </button>
                    );
                  })}
                </div>
              </section>

              {selectedEvent ? (
                <section className="mt-4">
                  <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm sm:grid-cols-2">
                    <MetaRow label="请求" value={metaRequest(selectedEvent)} mono />
                    <MetaRow label="请求 ID" value={selectedEvent.requestId} mono />
                    <MetaRow
                      label="类别"
                      value={
                        KIND_LABELS[String(selectedEvent.context?.kind ?? "")] ??
                        (selectedEvent.context?.kind as string | undefined) ??
                        null
                      }
                    />
                    <MetaRow
                      label="用户 ID"
                      value={
                        selectedEvent.userId === null ? "未登录" : String(selectedEvent.userId)
                      }
                    />
                    <MetaRow label="版本" value={selectedEvent.release} />
                    <MetaRow
                      label="视口"
                      value={(selectedEvent.context?.viewport as string | undefined) ?? null}
                    />
                    <MetaRow
                      label="日志器"
                      value={(selectedEvent.context?.logger as string | undefined) ?? null}
                      mono
                    />
                    <MetaRow label="浏览器" value={selectedEvent.userAgent} />
                  </dl>

                  {Array.isArray(selectedEvent.context?.breadcrumbs) &&
                  selectedEvent.context.breadcrumbs.length > 0 ? (
                    <div className="mt-3">
                      <h3 className="text-sm font-bold text-slate-800">操作路径</h3>
                      <ol className="mt-1.5 flex flex-wrap items-center gap-1 text-xs text-slate-600">
                        {selectedEvent.context.breadcrumbs.map((crumb, index) => (
                          <li
                            key={`${crumb}-${index}`}
                            className="rounded-md bg-slate-100 px-2 py-1 font-mono"
                          >
                            {crumb}
                          </li>
                        ))}
                      </ol>
                    </div>
                  ) : null}

                  <div className="mt-4">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-bold text-slate-800">堆栈</h3>
                      {selectedEvent.stack ? (
                        <button
                          type="button"
                          onClick={copyStack}
                          className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-700 transition hover:bg-slate-50"
                        >
                          <Clipboard size={14} aria-hidden="true" />
                          {copied ? "已复制" : "复制堆栈"}
                        </button>
                      ) : null}
                    </div>
                    <pre className="mt-2 max-h-96 overflow-auto rounded-xl border border-slate-200 bg-slate-900 px-3 py-3 font-mono text-xs leading-relaxed text-slate-100">
                      {selectedEvent.stack ?? "（该记录没有堆栈信息）"}
                    </pre>
                  </div>
                </section>
              ) : null}
            </>
          ) : null}
        </div>

        {group ? (
          <footer className="border-t border-slate-200 px-5 py-3">
            <label className="block text-xs font-bold text-slate-500" htmlFor="ops-note">
              处理备注
            </label>
            <input
              id="ops-note"
              type="text"
              value={note}
              maxLength={500}
              onChange={(event) => setNote(event.target.value)}
              placeholder="例如：已在 v1.2.3 修复"
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none transition focus:border-[var(--brand)]"
            />
            <div className="mt-2.5 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={saving}
                onClick={() => void applyStatus("resolved")}
                className="flex min-h-10 items-center gap-2 rounded-xl bg-emerald-600 px-3.5 py-2 text-sm font-bold text-white transition hover:bg-emerald-700 disabled:opacity-60"
              >
                <CheckCircle2 size={15} aria-hidden="true" />
                标记已解决
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() => void applyStatus("ignored")}
                className="flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
              >
                <Ban size={15} aria-hidden="true" />
                忽略
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() => void applyStatus("open")}
                className="flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
              >
                <RotateCcw size={15} aria-hidden="true" />
                重新打开
              </button>
              {saving ? (
                <span className="flex items-center gap-1.5 text-sm text-slate-500">
                  <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                  保存中
                </span>
              ) : null}
            </div>
            <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
              <Check size={12} aria-hidden="true" />
              标记已解决后若再次发生，会自动重新打开并计入回归。
            </p>
          </footer>
        ) : null}
      </section>
    </div>
  );
}

function metaRequest(event: OpsErrorEvent): string | null {
  const parts = [event.method, event.route].filter(Boolean);
  if (parts.length === 0) {
    return null;
  }
  const status = event.statusCode ? ` → ${event.statusCode}` : "";
  return `${parts.join(" ")}${status}`;
}

function MetaRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
}) {
  if (!value) {
    return null;
  }
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="shrink-0 text-slate-500">{label}</dt>
      <dd
        className={`min-w-0 flex-1 truncate text-slate-800 ${mono ? "font-mono text-xs" : ""}`}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}
