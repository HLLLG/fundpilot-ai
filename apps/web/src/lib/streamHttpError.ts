import { userFacingErrorMessage } from "@/lib/userFacingError";

export const SERVICE_STARTING_MESSAGE = "服务正在启动，请稍后再点一次。";

const STARTUP_CONNECT_ATTEMPTS = 4;
const DEFAULT_RETRY_AFTER_MS = 2_000;
const MAX_RETRY_AFTER_MS = 10_000;

export function formatStreamHttpError(
  raw: string,
  status: number,
  fallback: string,
): string {
  const text = raw.trim();
  if (!text) {
    return status === 503 ? SERVICE_STARTING_MESSAGE : fallback;
  }
  try {
    const payload = JSON.parse(text) as {
      detail?: unknown;
      state?: unknown;
    };
    if (isInitializingPayload(payload)) {
      return SERVICE_STARTING_MESSAGE;
    }
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail.trim();
    }
  } catch {
    // 非 JSON 时尽量保留后端已写好的中文。
  }
  if (text.startsWith("{") || text.startsWith("<")) {
    return status === 503 ? SERVICE_STARTING_MESSAGE : fallback;
  }
  return text;
}

export function isRetryableStartupUnavailable(status: number, body: string): boolean {
  if (status !== 503) {
    return false;
  }
  const text = body.trim();
  if (!text) {
    return true;
  }
  try {
    return isInitializingPayload(JSON.parse(text) as { detail?: unknown; state?: unknown });
  } catch {
    return /initialization in progress/i.test(text);
  }
}

export function isServiceStartingError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const message = error.message;
  return (
    message === SERVICE_STARTING_MESSAGE ||
    message.includes("service initialization in progress") ||
    /"state"\s*:\s*"initializing"/.test(message)
  );
}

export function parseRetryAfterMs(
  response: Response,
  fallbackMs = DEFAULT_RETRY_AFTER_MS,
): number {
  const raw = response.headers.get("Retry-After")?.trim();
  if (!raw) {
    return fallbackMs;
  }
  const seconds = Number(raw);
  if (!Number.isFinite(seconds) || seconds < 0) {
    return fallbackMs;
  }
  return Math.min(Math.round(seconds * 1000), MAX_RETRY_AFTER_MS);
}

export function streamHandoffFailureMessage(
  error: unknown,
  fallback: string,
  handoffHint: string,
): string {
  const message = userFacingErrorMessage(error, fallback);
  if (isServiceStartingError(error) || /服务正在启动|initialization in progress/.test(message)) {
    return SERVICE_STARTING_MESSAGE;
  }
  return `${message.replace(/[。.]+\s*$/, "")}。${handoffHint}`;
}

export type ReadyStreamResponse = Response & { body: ReadableStream<Uint8Array> };

export async function waitForReadyStreamResponse(
  request: () => Promise<Response>,
  options: {
    fallback: string;
    signal?: AbortSignal;
    maxAttempts?: number;
  },
): Promise<ReadyStreamResponse> {
  const maxAttempts = options.maxAttempts ?? STARTUP_CONNECT_ATTEMPTS;
  let lastError: Error | null = null;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    throwIfAborted(options.signal);
    const response = await request();
    if (response.ok && response.body) {
      return response as ReadyStreamResponse;
    }
    const text = await response.text();
    lastError = new Error(formatStreamHttpError(text, response.status, options.fallback));
    const canRetry =
      isRetryableStartupUnavailable(response.status, text) && attempt < maxAttempts - 1;
    if (!canRetry) {
      throw lastError;
    }
    await delay(parseRetryAfterMs(response), options.signal);
  }
  throw lastError ?? new Error(options.fallback);
}

function isInitializingPayload(payload: { detail?: unknown; state?: unknown }): boolean {
  return (
    payload.state === "initializing" ||
    payload.detail === "service initialization in progress"
  );
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
}

async function delay(ms: number, signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal);
  if (ms <= 0) {
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const timer = globalThis.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
