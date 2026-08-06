"use client";

/**
 * 浏览器端错误自动上报。
 *
 * 用户反馈"页面报错了"时，能拿到的往往只有一句描述。这个模块负责把真正可修复的
 * 证据（错误类型、堆栈、出错路由、操作路径）直接送到后端 `/admin/ops` 面板。
 *
 * 三条硬约束：
 * 1. **不能递归**：上报走原生 fetch，且跳过遥测自身的路径；否则"上报失败"会
 *    触发新一轮上报。
 * 2. **不能刷爆**：同一签名在窗口期内只报一次，单个会话总量也有上限。渲染死循环
 *    每秒能抛上千个同样的错，不设限会同时打爆用户网络和服务端存储。
 * 3. **不能影响用户**：全部 catch 吞掉，任何上报失败都不得冒泡到页面。
 */

import { API_BASE, RELEASE_TAG } from "@/lib/api/base";
import { getAccessToken } from "@/lib/auth";

export const CLIENT_ERROR_REPORT_PATH = "/api/telemetry/client-errors";

/** 遥测自身的请求一律不上报，否则失败时会自我放大。 */
const TELEMETRY_PATH_MARKER = "/api/telemetry/";

const MAX_REPORTS_PER_SESSION = 25;
const MAX_BREADCRUMBS = 12;
const DEDUPE_WINDOW_MS = 60_000;
const REPORT_TIMEOUT_MS = 5_000;
const MAX_MESSAGE_CHARS = 2000;
const MAX_STACK_CHARS = 20_000;
const MAX_COMPONENT_STACK_CHARS = 8000;
const MAX_BREADCRUMB_CHARS = 200;

export type ClientErrorKind =
  | "window_error"
  | "unhandled_rejection"
  | "react_render"
  | "resource_load"
  | "api_failure"
  | "manual";

export type ClientErrorLevel = "warning" | "error" | "fatal";

export type ClientErrorInput = {
  message: string;
  errorType?: string;
  stack?: string | null;
  componentStack?: string | null;
  kind?: ClientErrorKind;
  level?: ClientErrorLevel;
  /** 覆盖默认的 window.location.pathname，用于上报「请求失败」时的目标路径。 */
  path?: string;
  statusCode?: number | null;
  requestId?: string | null;
};

type ReportResult = { accepted: boolean; fingerprint: string | null } | null;

let installed = false;
let sentCount = 0;
const recentSignatures = new Map<string, number>();
const breadcrumbs: string[] = [];

function clip(value: unknown, limit: number): string {
  return String(value ?? "").slice(0, limit);
}

/** 记录一条用户操作痕迹，崩溃时随报告一起提交，用来复现路径。 */
export function addClientBreadcrumb(text: string): void {
  const entry = clip(text, MAX_BREADCRUMB_CHARS).trim();
  if (!entry) {
    return;
  }
  if (breadcrumbs[breadcrumbs.length - 1] === entry) {
    return;
  }
  breadcrumbs.push(entry);
  while (breadcrumbs.length > MAX_BREADCRUMBS) {
    breadcrumbs.shift();
  }
}

/** 把任意抛出物收敛成可上报的三元组。reject 的值可能根本不是 Error。 */
export function describeThrown(value: unknown): {
  errorType: string;
  message: string;
  stack: string | null;
} {
  if (value instanceof Error) {
    return {
      errorType: value.name || "Error",
      message: value.message || value.name || "Error",
      stack: value.stack ?? null,
    };
  }
  if (typeof value === "string") {
    return { errorType: "Error", message: value, stack: null };
  }
  if (value && typeof value === "object") {
    const candidate = value as { name?: unknown; message?: unknown; stack?: unknown };
    const message =
      typeof candidate.message === "string" && candidate.message
        ? candidate.message
        : safeStringify(value);
    return {
      errorType:
        typeof candidate.name === "string" && candidate.name
          ? candidate.name
          : "Error",
      message,
      stack: typeof candidate.stack === "string" ? candidate.stack : null,
    };
  }
  return { errorType: "Error", message: String(value), stack: null };
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function shouldSend(signature: string): boolean {
  if (sentCount >= MAX_REPORTS_PER_SESSION) {
    return false;
  }
  const now = Date.now();
  if (recentSignatures.size > 64) {
    for (const [key, seenAt] of recentSignatures) {
      if (now - seenAt > DEDUPE_WINDOW_MS) {
        recentSignatures.delete(key);
      }
    }
  }
  const seenAt = recentSignatures.get(signature);
  if (seenAt !== undefined && now - seenAt < DEDUPE_WINDOW_MS) {
    return false;
  }
  recentSignatures.set(signature, now);
  sentCount += 1;
  return true;
}

function currentPath(): string {
  if (typeof window === "undefined") {
    return "/";
  }
  return window.location.pathname || "/";
}

function viewport(): string | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return `${window.innerWidth}x${window.innerHeight}`;
}

/**
 * 上报一个客户端错误。永不抛异常、永不返回 rejected promise。
 *
 * 返回 fingerprint（服务端分组指纹），便于界面上把它当作"报障编号"展示给用户。
 */
export async function reportClientError(
  input: ClientErrorInput,
): Promise<ReportResult> {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const message = clip(input.message, MAX_MESSAGE_CHARS).trim();
    if (!message) {
      return null;
    }
    const stack = input.stack ? clip(input.stack, MAX_STACK_CHARS) : null;
    const kind = input.kind ?? "manual";
    const path = input.path ?? currentPath();
    // 签名只取堆栈首行：同一错误在不同时刻的行号通常一致，而完整堆栈可能因异步
    // 调用链不同而变化，用完整堆栈会导致同一个 bug 反复上报。
    const signature = [
      kind,
      input.errorType ?? "Error",
      message,
      stack ? stack.split("\n", 2)[1] ?? "" : "",
      path,
    ].join("|");
    if (!shouldSend(signature)) {
      return null;
    }

    const headers: Record<string, string> = { "Content-Type": "application/json" };
    // 端点是可选鉴权：带上 token 只是为了让服务端记下「是哪个用户遇到的」，
    // 没有 token（例如登录页崩溃）同样会被接收。
    const token = getAccessToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), REPORT_TIMEOUT_MS);
    try {
      const response = await fetch(`${API_BASE}${CLIENT_ERROR_REPORT_PATH}`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          message,
          errorType: clip(input.errorType ?? "Error", 180),
          stack,
          componentStack: input.componentStack
            ? clip(input.componentStack, MAX_COMPONENT_STACK_CHARS)
            : null,
          level: input.level ?? "error",
          kind,
          path: clip(path, 240),
          release: RELEASE_TAG,
          requestId: input.requestId ?? null,
          statusCode: input.statusCode ?? null,
          viewport: viewport(),
          referrer: document.referrer ? clip(document.referrer, 500) : undefined,
          breadcrumbs: [...breadcrumbs],
        }),
        // 页面正在卸载时（导航中崩溃）也要把请求发出去。
        keepalive: true,
        signal: controller.signal,
      });
      if (!response.ok) {
        return null;
      }
      return (await response.json()) as ReportResult;
    } finally {
      window.clearTimeout(timeoutId);
    }
  } catch {
    // 上报失败就算了：绝不能因为遥测而影响用户。
    return null;
  }
}

/**
 * 上报一次 API 调用失败。由 apiFetch 调用。
 *
 * 只覆盖服务端看不到的盲区：网络层失败、网关错误、超时。普通 500 已经在后端
 * 连同完整 traceback 记录过了，前端再报一次只会把同一故障拆成两个分组。
 */
export function reportApiFailure(input: {
  url: string;
  errorType: string;
  message: string;
  statusCode?: number | null;
  requestId?: string | null;
}): void {
  if (input.url.includes(TELEMETRY_PATH_MARKER)) {
    return;
  }
  const target = safePathOf(input.url);
  addClientBreadcrumb(`api-fail:${target}`);
  void reportClientError({
    kind: "api_failure",
    errorType: input.errorType,
    message: input.message,
    // 用后端路由而不是当前页面路径，这样分组会按失败的接口聚合。
    path: target,
    statusCode: input.statusCode ?? null,
    requestId: input.requestId ?? null,
  });
}

/** 取 URL 的 pathname，避免把查询参数带进上报。 */
export function safePathOf(url: string): string {
  try {
    return new URL(url, API_BASE).pathname || "/";
  } catch {
    return url.split("?", 1)[0] || "/";
  }
}

function isResourceLoadFailure(event: ErrorEvent): boolean {
  const target = event.target;
  return Boolean(
    target && target !== window && (target as HTMLElement).tagName !== undefined,
  );
}

function resourceUrl(target: EventTarget | null): string {
  const element = target as (HTMLElement & { src?: string; href?: string }) | null;
  return element?.src || element?.href || "unknown";
}

/**
 * 挂上全局监听。返回卸载函数，重复调用是安全的（只装一次）。
 */
export function installClientErrorReporter(): () => void {
  if (typeof window === "undefined" || installed) {
    return () => undefined;
  }
  installed = true;

  const onError = (event: ErrorEvent) => {
    if (isResourceLoadFailure(event)) {
      // 静态资源 404/被拦截：常见于发版后旧页面请求已删除的 chunk，
      // 表现为用户点击无反应，控制台之外毫无痕迹。
      const url = resourceUrl(event.target);
      void reportClientError({
        kind: "resource_load",
        errorType: "ResourceLoadError",
        level: "warning",
        message: `资源加载失败：${(event.target as HTMLElement).tagName} ${safePathOf(url)}`,
      });
      return;
    }
    const described = describeThrown(event.error ?? event.message);
    void reportClientError({
      kind: "window_error",
      errorType: described.errorType,
      message: described.message,
      stack:
        described.stack ??
        (event.filename ? `at ${event.filename}:${event.lineno}:${event.colno}` : null),
    });
  };

  const onRejection = (event: PromiseRejectionEvent) => {
    const described = describeThrown(event.reason);
    void reportClientError({
      kind: "unhandled_rejection",
      errorType: described.errorType,
      message: described.message,
      stack: described.stack,
    });
  };

  // 捕获阶段：资源加载失败的 error 事件不会冒泡，只能在这里拿到。
  window.addEventListener("error", onError, true);
  window.addEventListener("unhandledrejection", onRejection);

  return () => {
    window.removeEventListener("error", onError, true);
    window.removeEventListener("unhandledrejection", onRejection);
    installed = false;
  };
}

/** 仅供测试：清空会话内的去重与配额状态。 */
export function resetClientErrorReporterForTests(): void {
  installed = false;
  sentCount = 0;
  recentSignatures.clear();
  breadcrumbs.length = 0;
}
