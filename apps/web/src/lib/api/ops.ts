import { API_BASE, ApiError, apiFetch } from "@/lib/api/core";

/**
 * 运维监控面板（/admin/ops）的 API 客户端。
 *
 * 字段命名故意不做统一：后端的聚合指标沿用 performance 契约的 snake_case，
 * 而错误分组与采集状态沿用 admin 契约的 camelCase。这里按后端实际返回原样声明，
 * 避免多一层易腐化的转换层。
 */

export type OpsErrorSource = "frontend" | "backend" | "worker";
export type OpsErrorLevel = "warning" | "error" | "fatal";
export type OpsErrorStatus = "open" | "resolved" | "ignored";
export type OpsSourceFilter = "all" | OpsErrorSource;
export type OpsStatusFilter = "all" | OpsErrorStatus;

export type OpsErrorKind =
  | "window_error"
  | "unhandled_rejection"
  | "react_render"
  | "resource_load"
  | "api_failure"
  | "manual";

/** 一个错误分组（同一指纹的所有发生次数）。 */
export type OpsErrorGroup = {
  fingerprint: string;
  source: OpsErrorSource | string;
  level: OpsErrorLevel | string;
  errorType: string;
  message: string;
  route: string | null;
  status: OpsErrorStatus | string;
  firstSeenAt: string | null;
  lastSeenAt: string | null;
  /** 累计发生次数（含被采样丢弃的明细）。 */
  eventCount: number;
  /** 当前时间窗口内的发生次数。 */
  windowEventCount: number;
  affectedUserCount: number;
  resolvedAt: string | null;
  resolvedBy: number | null;
  note: string | null;
};

/** 一次具体的发生记录，带完整堆栈与请求上下文。 */
export type OpsErrorEvent = {
  eventId: string;
  occurredAt: string | null;
  source: OpsErrorSource | string;
  level: OpsErrorLevel | string;
  errorType: string;
  message: string;
  stack: string | null;
  route: string | null;
  method: string | null;
  statusCode: number | null;
  requestId: string | null;
  userId: number | null;
  release: string | null;
  userAgent: string | null;
  context: OpsErrorEventContext | null;
};

export type OpsErrorEventContext = {
  kind?: OpsErrorKind | string;
  viewport?: string;
  referrer?: string;
  breadcrumbs?: string[];
  logger?: string;
  module?: string;
  function?: string;
  lineno?: number;
  thread?: string;
  process?: number;
  task?: string;
  handler?: string;
  [key: string]: unknown;
};

export type OpsTrafficPoint = {
  /** 固定宽度的 UTC 时刻（YYYY-MM-DDTHH:mm:ssZ），可直接 new Date()。 */
  bucket_start: string;
  request_count: number;
  server_error_count: number;
  client_error_count: number;
  mean_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  response_bytes: number;
};

export type OpsRouteRow = {
  method: string;
  route: string;
  request_count: number;
  server_error_count: number;
  client_error_count: number;
  server_error_rate_percent: number;
  mean_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
};

export type OpsCaptureState = {
  errorCaptureEnabled: boolean;
  trafficCaptureEnabled: boolean;
  clientIngestEnabled: boolean;
  errorRetentionDays: number;
  trafficRetentionDays: number;
  instanceId: string;
  writerThreadAlive: boolean;
  queueDepth: number;
  droppedEventCount: number;
  droppedTrafficCount: number;
  pendingTrafficBuckets: number;
  pendingRouteBuckets: number;
  persistFailureCount: number;
};

export type OpsOverview = {
  contract_version: string;
  /** false 表示聚合查询失败，面板应降级提示而不是显示成"零流量"。 */
  available: boolean;
  generated_at: string;
  window: {
    hours: number;
    start: string;
    end: string;
    bucket_seconds: number;
  };
  totals: {
    request_count: number;
    server_error_count: number;
    client_error_count: number;
    server_error_rate_percent: number;
    mean_ms: number | null;
    p95_ms: number | null;
    p99_ms: number | null;
    max_ms: number | null;
    response_bytes: number;
    requests_per_minute: number;
  };
  series: OpsTrafficPoint[];
  errors: {
    event_count: number;
    frontend_event_count: number;
    backend_event_count: number;
    group_count: number;
    open_group_count: number;
    new_group_count: number;
    open_group_count_all_time: number;
    window_start: string;
  };
  top_error_groups: OpsErrorGroup[];
  top_routes: OpsRouteRow[];
  capture: OpsCaptureState;
  notes: {
    percentile_basis: string;
    privacy: string;
  };
};

export type OpsErrorGroupPage = {
  contract_version: string;
  items: OpsErrorGroup[];
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  window: { hours: number; start: string };
  filters: { source: string; status: string; query: string };
};

export type OpsErrorGroupDetail = {
  contract_version: string;
  group: OpsErrorGroup;
  /** 实际留存的明细条数，可能小于 eventCount（高频错误按分钟采样）。 */
  storedEventCount: number;
  events: OpsErrorEvent[];
  hourly: Array<{ hour: string; eventCount: number }>;
  window: { hours: number; start: string };
};

async function responseJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : fallback;
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export async function fetchOpsOverview(hours = 24): Promise<OpsOverview> {
  const params = new URLSearchParams({ hours: String(hours) });
  const response = await apiFetch(`${API_BASE}/api/admin/ops/overview?${params}`, {
    cache: "no-store",
  });
  return responseJson(response, "无法读取运维概览");
}

export async function fetchOpsErrorGroups(
  options: {
    hours?: number;
    source?: OpsSourceFilter;
    status?: OpsStatusFilter;
    query?: string;
    page?: number;
    pageSize?: number;
  } = {},
): Promise<OpsErrorGroupPage> {
  const params = new URLSearchParams({
    hours: String(options.hours ?? 24),
    source: options.source ?? "all",
    status: options.status ?? "open",
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 20),
  });
  if (options.query) {
    params.set("query", options.query);
  }
  const response = await apiFetch(`${API_BASE}/api/admin/ops/errors?${params}`, {
    cache: "no-store",
  });
  return responseJson(response, "无法读取错误列表");
}

export async function fetchOpsErrorGroup(
  fingerprint: string,
  options: { hours?: number; eventLimit?: number } = {},
): Promise<OpsErrorGroupDetail> {
  const params = new URLSearchParams({
    hours: String(options.hours ?? 168),
    event_limit: String(options.eventLimit ?? 20),
  });
  const response = await apiFetch(
    `${API_BASE}/api/admin/ops/errors/${encodeURIComponent(fingerprint)}?${params}`,
    { cache: "no-store" },
  );
  return responseJson(response, "无法读取错误详情");
}

export async function updateOpsErrorStatus(
  fingerprint: string,
  payload: { status: OpsErrorStatus; note?: string | null },
): Promise<OpsErrorGroup> {
  const response = await apiFetch(
    `${API_BASE}/api/admin/ops/errors/${encodeURIComponent(fingerprint)}/status`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: payload.status, note: payload.note ?? null }),
    },
  );
  return responseJson(response, "更新错误状态失败");
}

export async function fetchOpsCaptureState(): Promise<OpsCaptureState> {
  const response = await apiFetch(`${API_BASE}/api/admin/ops/capture`, {
    cache: "no-store",
  });
  return responseJson(response, "无法读取采集状态");
}
