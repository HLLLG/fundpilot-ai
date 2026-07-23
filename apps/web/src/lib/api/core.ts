import { clearAccessToken, getAccessToken } from "@/lib/auth";


export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";


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
      throw new ApiError(`请求超时（${Math.round(timeoutMs / 1000)} 秒）`, 408);
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
  return response;
}
