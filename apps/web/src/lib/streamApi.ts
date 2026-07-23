import type { Holding, InvestorProfile, Report } from "@/lib/api";
import { apiFetch } from "@/lib/api/core";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

/** Wall-clock ms for stream session timing (event handlers only). */
export function streamTimestamp(): number {
  return Date.now();
}

export type StreamingPartialField =
  | "title"
  | "summary"
  | "fund_recommendation"
  | "caveats";

export type FundRecommendationPartial = Report["fund_recommendations"][number];

export type StreamingStageEntry = {
  stage: string;
  label: string;
  at: number;
};

export const PRE_LLM_FOLLOWUP_STAGES = new Set([
  "fund_data",
  "news_prefetch",
  "news_summarize",
]);

export type StreamingReportState = {
  stage: string;
  stageLabel: string;
  fundCodes: string[];
  fundNames: string[];
  title?: string;
  summary?: string;
  partialByCode: Record<string, Partial<FundRecommendationPartial>>;
  caveats?: string[];
  stageLog: StreamingStageEntry[];
  thinkingNotes: string[];
  startedAt: number;
  /** 累积 LLM token 原文（阶段 4.1 打字机预览，上限 2KB） */
  tokenBuffer: string;
  sessionId?: string;
  followupNotes: string[];
  backgroundJobId?: string;
  backgroundFallbackReason?: string;
};

export interface StreamingReportEvents {
  onSession?: (sessionId: string) => void;
  onStage?: (stage: string, label: string) => void;
  onSkeleton?: (fundCodes: string[], fundNames: string[]) => void;
  onToken?: (content: string) => void;
  onPartial?: (field: StreamingPartialField, value: unknown) => void;
  onDone?: (report: Report) => void;
  onError?: (message: string) => void;
}

type StreamEvent =
  | { type: "session"; session_id: string }
  | { type: "stage"; stage: string; label: string }
  | { type: "skeleton"; fund_codes: string[]; fund_names: string[] }
  | { type: "token"; content: string }
  | {
      type: "report_partial";
      field: StreamingPartialField;
      value: unknown;
    }
  | { type: "done"; report_id: string; report: Report }
  | { type: "error"; message: string };

const FIRST_EVENT_TIMEOUT_MS = 5000;
const STREAM_IDLE_TIMEOUT_MS = 120_000;
export const STREAM_TOKEN_BUFFER_MAX = 2048;

export function appendStreamTokenBuffer(prev: string, chunk: string): string {
  const next = prev + chunk;
  if (next.length <= STREAM_TOKEN_BUFFER_MAX) {
    return next;
  }
  return next.slice(-STREAM_TOKEN_BUFFER_MAX);
}

export function markStreamingReportBackgroundFallback(
  current: StreamingReportState | null,
  jobId: string,
  reason: string,
): StreamingReportState | null {
  if (!current) {
    return current;
  }
  const note = `已切换到后台分析：${reason}`;
  const thinkingNotes = current.thinkingNotes.includes(note)
    ? current.thinkingNotes
    : [...current.thinkingNotes, note];
  return {
    ...current,
    stageLabel: `已切换到后台分析，当前停在：${current.stageLabel}`,
    thinkingNotes,
    backgroundJobId: jobId,
    backgroundFallbackReason: reason,
  };
}

function analysisPayload(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
  systemRolePrompt?: string | null,
) {
  return {
    holdings,
    profile,
    ocr_text: ocrText,
    analysis_mode: "deep",
    system_role_prompt: systemRolePrompt?.trim() || null,
  };
}

function parseSseLine(line: string): StreamEvent | null {
  if (!line.startsWith("data: ")) {
    return null;
  }
  try {
    return JSON.parse(line.slice(6)) as StreamEvent;
  } catch {
    return null;
  }
}

function dispatchEvent(event: StreamEvent, events: StreamingReportEvents): "continue" | "done" | "error" {
  if (event.type === "session") {
    events.onSession?.(event.session_id);
    return "continue";
  }
  if (event.type === "stage") {
    events.onStage?.(event.stage, event.label);
    return "continue";
  }
  if (event.type === "skeleton") {
    events.onSkeleton?.(event.fund_codes, event.fund_names);
    return "continue";
  }
  if (event.type === "token") {
    events.onToken?.(event.content);
    return "continue";
  }
  if (event.type === "report_partial") {
    events.onPartial?.(event.field, event.value);
    return "continue";
  }
  if (event.type === "done") {
    events.onDone?.(event.report);
    return "done";
  }
  if (event.type === "error") {
    events.onError?.(event.message);
    return "error";
  }
  return "continue";
}

export async function streamAnalysis(
  holdings: Holding[],
  profile: InvestorProfile,
  events: StreamingReportEvents,
  options?: {
    ocrText?: string;
    systemRolePrompt?: string | null;
    signal?: AbortSignal;
    idleTimeoutMs?: number;
  },
): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/analyze/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(
      analysisPayload(
        holdings,
        profile,
        options?.ocrText,
        options?.systemRolePrompt,
      ),
    ),
    signal: options?.signal,
    timeoutMs: 0,
  });
  if (!response.ok || !response.body) {
    throw new Error(await response.text());
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawEvent = false;
  let idleTimedOut = false;

  const timeoutAbort = new AbortController();
  const linkedSignal = options?.signal;
  if (linkedSignal) {
    if (linkedSignal.aborted) {
      timeoutAbort.abort();
    } else {
      linkedSignal.addEventListener("abort", () => timeoutAbort.abort(), { once: true });
    }
  }

  const timeoutId = window.setTimeout(() => {
    if (!sawEvent) {
      timeoutAbort.abort();
      reader.cancel().catch(() => undefined);
    }
  }, FIRST_EVENT_TIMEOUT_MS);
  const idleTimeoutMs = options?.idleTimeoutMs ?? STREAM_IDLE_TIMEOUT_MS;
  let idleTimeoutId: number | null = null;
  const clearIdleTimeout = () => {
    if (idleTimeoutId !== null) {
      window.clearTimeout(idleTimeoutId);
      idleTimeoutId = null;
    }
  };
  const resetIdleTimeout = () => {
    clearIdleTimeout();
    idleTimeoutId = window.setTimeout(() => {
      idleTimedOut = true;
      timeoutAbort.abort();
      reader.cancel().catch(() => undefined);
    }, idleTimeoutMs);
  };

  try {
    while (true) {
      if (linkedSignal?.aborted) {
        await reader.cancel().catch(() => undefined);
        throw new DOMException("The operation was aborted.", "AbortError");
      }
      if (idleTimedOut) {
        throw new Error("流式生成长时间没有进展 (long time without progress)，已切换到后台任务。");
      }
      if (timeoutAbort.signal.aborted && !sawEvent) {
        throw new Error("流式连接超时，将回退到异步分析");
      }
      const { done, value } = await reader.read();
      if (idleTimedOut) {
        throw new Error("流式生成长时间没有进展 (long time without progress)，已切换到后台任务。");
      }
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        for (const line of part.split("\n")) {
          const event = parseSseLine(line.trim());
          if (!event) {
            continue;
          }
          sawEvent = true;
          window.clearTimeout(timeoutId);
          resetIdleTimeout();
          const outcome = dispatchEvent(event, events);
          if (outcome === "done") {
            return;
          }
          if (outcome === "error") {
            throw new Error("stream error");
          }
        }
      }
    }
  } finally {
    window.clearTimeout(timeoutId);
    clearIdleTimeout();
  }

  if (!sawEvent) {
    throw new Error("流式连接未收到事件");
  }
}

export async function submitStreamFollowup(sessionId: string, message: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/analyze/stream/${sessionId}/followup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "追加说明失败");
  }
}
