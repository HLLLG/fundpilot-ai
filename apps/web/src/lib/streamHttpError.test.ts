import { describe, expect, it } from "vitest";

import {
  SERVICE_STARTING_MESSAGE,
  formatStreamHttpError,
  isRetryableStartupUnavailable,
  isServiceStartingError,
  parseRetryAfterMs,
  streamHandoffFailureMessage,
  waitForReadyStreamResponse,
} from "@/lib/streamHttpError";

const initializingBody = JSON.stringify({
  detail: "service initialization in progress",
  ready: false,
  state: "initializing",
  started_at: "2026-08-24T03:37:55.807759+00:00",
  ready_at: null,
  failure_category: null,
});

describe("formatStreamHttpError", () => {
  it("maps readiness-gate JSON to a short Chinese instruction", () => {
    expect(formatStreamHttpError(initializingBody, 503, "连接失败")).toBe(
      SERVICE_STARTING_MESSAGE,
    );
  });

  it("does not leak raw JSON or HTML when the body is not a user message", () => {
    expect(formatStreamHttpError('{"oops":true}', 503, "连接失败")).toBe(
      SERVICE_STARTING_MESSAGE,
    );
    expect(formatStreamHttpError("<html>bad gateway</html>", 502, "连接失败")).toBe(
      "连接失败",
    );
  });

  it("keeps a backend Chinese detail", () => {
    expect(formatStreamHttpError('{"detail":"持仓为空"}', 400, "连接失败")).toBe("持仓为空");
  });
});

describe("startup retry classification", () => {
  it("retries only initializing 503s", () => {
    expect(isRetryableStartupUnavailable(503, initializingBody)).toBe(true);
    expect(isRetryableStartupUnavailable(503, "")).toBe(true);
    expect(isRetryableStartupUnavailable(503, '{"detail":"too many requests"}')).toBe(false);
    expect(isRetryableStartupUnavailable(400, initializingBody)).toBe(false);
  });

  it("reads Retry-After seconds and caps the wait", () => {
    expect(parseRetryAfterMs(new Response(null, { headers: { "Retry-After": "2" } }))).toBe(
      2000,
    );
    expect(parseRetryAfterMs(new Response(null, { headers: { "Retry-After": "30" } }))).toBe(
      10_000,
    );
    expect(parseRetryAfterMs(new Response(null))).toBe(2000);
  });
});

describe("streamHandoffFailureMessage", () => {
  it("does not append the background-job hint for a startup error", () => {
    expect(
      streamHandoffFailureMessage(
        new Error(SERVICE_STARTING_MESSAGE),
        "流式生成中断",
        "没有转入后台任务，请再点一次生成日报。",
      ),
    ).toBe(SERVICE_STARTING_MESSAGE);
    expect(
      streamHandoffFailureMessage(
        new Error(initializingBody),
        "流式生成中断",
        "没有转入后台任务，请再点一次生成日报。",
      ),
    ).toBe(SERVICE_STARTING_MESSAGE);
  });

  it("still explains a mid-stream drop", () => {
    expect(
      streamHandoffFailureMessage(
        new Error("流式连接波动"),
        "流式生成中断",
        "没有转入后台任务，请再点一次生成日报。",
      ),
    ).toBe("流式连接波动。没有转入后台任务，请再点一次生成日报。");
  });
});

describe("waitForReadyStreamResponse", () => {
  it("retries an initializing 503 and then returns the stream", async () => {
    const encoder = new TextEncoder();
    let calls = 0;
    const request = async () => {
      calls += 1;
      if (calls === 1) {
        return new Response(initializingBody, {
          status: 503,
          headers: { "Retry-After": "0", "Content-Type": "application/json" },
        });
      }
      return new Response(encoder.encode("data: ok\n\n"), { status: 200 });
    };

    const response = await waitForReadyStreamResponse(request, { fallback: "连接失败" });
    expect(response.ok).toBe(true);
    expect(calls).toBe(2);
  });

  it("gives up with a Chinese message after initializing retries", async () => {
    const request = async () =>
      new Response(initializingBody, {
        status: 503,
        headers: { "Retry-After": "0" },
      });

    await expect(
      waitForReadyStreamResponse(request, { fallback: "连接失败", maxAttempts: 2 }),
    ).rejects.toThrow(SERVICE_STARTING_MESSAGE);
  });
});

describe("isServiceStartingError", () => {
  it("recognizes both the mapped message and leaked readiness JSON", () => {
    expect(isServiceStartingError(new Error(SERVICE_STARTING_MESSAGE))).toBe(true);
    expect(isServiceStartingError(new Error(initializingBody))).toBe(true);
    expect(isServiceStartingError(new Error("流式连接波动"))).toBe(false);
  });
});
