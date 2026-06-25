import { getAccessToken } from "@/lib/auth";
import type {
  AnalysisMode,
  DiscoveryRecommendation,
  DiscoveryScanMode,
  FundDiscoveryReport,
  FundTypePreference,
  Holding,
  InvestorProfile,
  SelectionStrategy,
} from "@/lib/api";
import {
  appendStreamTokenBuffer,
  STREAM_TOKEN_BUFFER_MAX,
} from "@/lib/streamApi";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(input, { ...init, headers });
}

export type DiscoveryPartialField =
  | "title"
  | "summary"
  | "recommendation"
  | "caveats";

export type DiscoveryRecommendationPartial = Partial<DiscoveryRecommendation>;

export type DiscoveryStageEntry = {
  stage: string;
  label: string;
  at: number;
};

export type StreamingDiscoveryState = {
  stage: string;
  stageLabel: string;
  fundCodes: string[];
  fundNames: string[];
  title?: string;
  summary?: string;
  partialByCode: Record<string, DiscoveryRecommendationPartial>;
  caveats?: string[];
  stageLog: DiscoveryStageEntry[];
  tokenBuffer: string;
  startedAt: number;
};

export interface StreamingDiscoveryEvents {
  onStage?: (stage: string, label: string) => void;
  onSkeleton?: (fundCodes: string[], fundNames: string[]) => void;
  onToken?: (content: string) => void;
  onPartial?: (field: DiscoveryPartialField, value: unknown) => void;
  onDone?: (report: FundDiscoveryReport) => void;
  onError?: (message: string) => void;
}

type StreamEvent =
  | { type: "stage"; stage: string; label: string }
  | { type: "skeleton"; fund_codes: string[]; fund_names: string[] }
  | { type: "token"; content: string }
  | {
      type: "report_partial";
      field: DiscoveryPartialField;
      value: unknown;
    }
  | { type: "done"; report_id: string; report: FundDiscoveryReport }
  | { type: "error"; message: string };

const FIRST_EVENT_TIMEOUT_MS = 5000;

export { appendStreamTokenBuffer, STREAM_TOKEN_BUFFER_MAX };

function discoveryPayload(
  holdings: Holding[],
  profile: InvestorProfile,
  options?: {
    analysisMode?: AnalysisMode;
    focusSectors?: string[];
    budgetYuan?: number | null;
    fundTypePreference?: FundTypePreference;
    selectionStrategy?: SelectionStrategy;
    scanMode?: DiscoveryScanMode;
    dipLookbackDays?: number;
    dipMinDropPercent?: number;
    systemRolePrompt?: string | null;
  },
) {
  return {
    holdings,
    profile,
    analysis_mode: options?.analysisMode ?? "fast",
    focus_sectors: options?.focusSectors ?? [],
    budget_yuan: options?.budgetYuan ?? null,
    fund_type_preference: options?.fundTypePreference ?? "any",
    selection_strategy: options?.selectionStrategy ?? "balanced",
    scan_mode: options?.scanMode ?? "full_market",
    dip_lookback_days: options?.dipLookbackDays ?? 5,
    dip_min_drop_percent: options?.dipMinDropPercent ?? 3.0,
    system_role_prompt: options?.systemRolePrompt?.trim() || null,
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

function dispatchEvent(
  event: StreamEvent,
  events: StreamingDiscoveryEvents,
): "continue" | "done" | "error" {
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

export async function streamDiscovery(
  holdings: Holding[],
  profile: InvestorProfile,
  events: StreamingDiscoveryEvents,
  options?: {
    analysisMode?: AnalysisMode;
    focusSectors?: string[];
    budgetYuan?: number | null;
    fundTypePreference?: FundTypePreference;
    selectionStrategy?: SelectionStrategy;
    scanMode?: DiscoveryScanMode;
    dipLookbackDays?: number;
    dipMinDropPercent?: number;
    systemRolePrompt?: string | null;
    signal?: AbortSignal;
  },
): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/fund-discovery/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(discoveryPayload(holdings, profile, options)),
    signal: options?.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(await response.text());
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawEvent = false;

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

  try {
    while (true) {
      if (linkedSignal?.aborted) {
        await reader.cancel().catch(() => undefined);
        throw new DOMException("The operation was aborted.", "AbortError");
      }
      if (timeoutAbort.signal.aborted && !sawEvent) {
        throw new Error("流式连接超时，将回退到异步扫描");
      }
      const { done, value } = await reader.read();
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
  }

  if (!sawEvent) {
    throw new Error("流式连接未收到事件");
  }
}
