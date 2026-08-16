"use client";

import { useEffect, useState } from "react";
import { GitBranch, Loader2 } from "lucide-react";

import { fetchGraphRun, fetchGraphRuns, type GraphRun } from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";
import { StatusPill } from "@/components/StatusPill";
import { userFacingErrorMessage } from "@/lib/userFacingError";

const GRAPH_LABELS: Record<string, string> = {
  chat_followup: "追问",
  daily_report: "日报任务",
  daily_report_stream: "日报流式",
  discovery_scan: "荐基任务",
  discovery_scan_stream: "荐基流式",
};

const OWNER_LABELS: Record<string, string> = {
  code: "代码",
  worker: "模型工人",
  agent: "模型编排",
};

function ownerTone(owner?: string | null): "blue" | "green" | "amber" {
  if (owner === "agent") {
    return "amber";
  }
  if (owner === "worker") {
    return "blue";
  }
  return "green";
}

function statusTone(status: string): "blue" | "green" | "amber" | "red" {
  if (status === "completed") {
    return "green";
  }
  if (status === "failed") {
    return "red";
  }
  if (status === "running") {
    return "amber";
  }
  return "blue";
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function GraphRunsPanel() {
  const [runs, setRuns] = useState<GraphRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<GraphRun | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchGraphRuns(12)
      .then((result) => {
        if (!cancelled) {
          setRuns(result);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(userFacingErrorMessage(loadError, "管线轨迹加载失败"));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [retrySequence]);

  useEffect(() => {
    if (!openId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    void fetchGraphRun(openId)
      .then((result) => {
        if (!cancelled) {
          setDetail(result);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDetail(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [openId]);

  return (
    <section className="section-card p-5" data-testid="graph-runs-panel">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-lg font-black text-[var(--brand-deep)]">
            <GitBranch className="h-4 w-4" aria-hidden />
            管线轨迹
          </h3>
          <p className="mt-1 text-sm text-slate-500">
            LangGraph 节点记录。只存节点名与归属，不含 Prompt、持仓或工具原文。
          </p>
        </div>
      </div>

      {loading && !runs.length ? (
        <div className="mt-3 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          正在读取最近轨迹…
        </div>
      ) : null}

      {error && !runs.length ? (
        <InlineNotice
          tone="error"
          message={error}
          action={{
            label: "重试",
            onClick: () => setRetrySequence((current) => current + 1),
          }}
          className="mt-3"
        />
      ) : null}

      {!loading && !error && !runs.length ? (
        <p className="mt-3 text-sm text-slate-500">还没有可查看的节点轨迹。生成日报、荐基或深度追问后会出现在这里。</p>
      ) : null}

      <ul className="mt-4 grid gap-2">
        {runs.map((run) => {
          const open = openId === run.id;
          return (
            <li key={run.id} className="rounded-xl border border-slate-200/80 bg-white/70 px-3 py-2">
              <button
                type="button"
                className="flex w-full items-center justify-between gap-3 text-left"
                onClick={() => setOpenId(open ? null : run.id)}
                aria-expanded={open}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-slate-800">
                    {GRAPH_LABELS[run.graph_name] ?? run.graph_name}
                  </span>
                  <span className="block truncate text-xs text-slate-500">{formatTime(run.created_at)}</span>
                </span>
                <StatusPill tone={statusTone(run.status)}>
                  {run.status === "completed" ? "完成" : run.status === "failed" ? "失败" : "进行中"}
                </StatusPill>
              </button>
              {open ? (
                <ol className="mt-3 grid gap-1.5 border-t border-slate-100 pt-3 text-xs text-slate-600">
                  {(detail?.id === run.id ? detail.events : null)?.map((event) => (
                    <li key={`${event.seq}-${event.event_type}`} className="flex items-center justify-between gap-2">
                      <span className="truncate">
                        {event.node || event.event_type}
                        {event.event_type !== "node_end" && event.event_type !== "stage"
                          ? ` · ${event.event_type}`
                          : ""}
                      </span>
                      <StatusPill tone={ownerTone(event.owner)}>
                        {OWNER_LABELS[event.owner ?? ""] ?? event.owner ?? "代码"}
                      </StatusPill>
                    </li>
                  )) ?? (
                    <li className="flex items-center gap-2 text-slate-400">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                      读取节点…
                    </li>
                  )}
                </ol>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
