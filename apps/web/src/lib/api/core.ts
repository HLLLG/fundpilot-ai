import { clearAccessToken, getAccessToken } from "@/lib/auth";
import { reportApiFailure } from "@/lib/clientErrorReporter";

// 从 base.ts 转出，保持既有 `import { API_BASE } from "@/lib/api/core"` 不变。
export { API_BASE } from "@/lib/api/base";


export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type ApiFetchInit = RequestInit & {
  /** Set to 0 only for an explicitly long-lived transport such as SSE. */
  timeoutMs?: number;
};

const DEFAULT_API_TIMEOUT_MS = 60_000;

/** 502/503/504 由反向代理产生，应用日志里往往完全没有痕迹。 */
const GATEWAY_ERROR_STATUSES = new Set([502, 503, 504]);


function isAuthEntrypoint(url: string): boolean {
  return url.includes("/api/auth/login") || url.includes("/api/auth/register");
}


function redirectToLogin(): void {
  if (typeof window === "undefined") {
    return;
  }
  const path = window.location.pathname;
  if (path === "/login" || path === "/register") {
    return;
  }
  const redirect = encodeURIComponent(path + window.location.search);
  window.location.href = `/login?redirect=${redirect}`;
}


export async function apiFetch(input: string, init?: ApiFetchInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const { timeoutMs = DEFAULT_API_TIMEOUT_MS, signal: upstreamSignal, ...requestInit } =
    init ?? {};
  const controller = timeoutMs > 0 ? new AbortController() : null;
  let timedOut = false;
  const forwardAbort = () => controller?.abort(upstreamSignal?.reason);
  if (controller) {
    if (upstreamSignal?.aborted) {
      forwardAbort();
    } else {
      upstreamSignal?.addEventListener("abort", forwardAbort, { once: true });
    }
  }
  const timeoutId =
    timeoutMs > 0
      ? globalThis.setTimeout(() => {
          timedOut = true;
          controller?.abort(new DOMException("API request timed out", "TimeoutError"));
        }, timeoutMs)
      : null;
  let response: Response;
  try {
    response = await fetch(input, {
      ...requestInit,
      headers,
      signal: controller?.signal ?? upstreamSignal,
    });
  } catch (error) {
    if (timedOut) {
      reportApiFailure({
        url: input,
        errorType: "TimeoutError",
        message: `请求超时：${Math.round(timeoutMs / 1000)} 秒内未返回`,
        statusCode: 408,
      });
      throw new ApiError(`请求超时（${Math.round(timeoutMs / 1000)} 秒）`, 408);
    }
    // 调用方主动取消（切页、组件卸载）不是故障，不上报。
    if (!upstreamSignal?.aborted) {
      reportApiFailure({
        url: input,
        errorType: "NetworkError",
        message: `请求未能送达：${error instanceof Error ? error.message : String(error)}`,
      });
    }
    throw error;
  } finally {
    if (timeoutId !== null) {
      globalThis.clearTimeout(timeoutId);
    }
    if (controller) {
      upstreamSignal?.removeEventListener("abort", forwardAbort);
    }
  }
  if (
    response.status === 401 &&
    typeof window !== "undefined" &&
    token &&
    getAccessToken() === token &&
    !isAuthEntrypoint(input)
  ) {
    clearAccessToken();
    redirectToLogin();
  }
  // 只上报后端观测不到的那部分：网关层错误说明请求可能根本没到应用进程。
  // 普通 500 后端已连同 traceback 记录，前端重复上报会把同一故障拆成两个分组。
  if (GATEWAY_ERROR_STATUSES.has(response.status)) {
    reportApiFailure({
      url: input,
      errorType: `GatewayError${response.status}`,
      message: `网关返回 ${response.status}，请求可能未到达应用`,
      statusCode: response.status,
      requestId: response.headers.get("X-Request-ID"),
    });
  }
  return response;
}
