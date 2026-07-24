import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { streamDiscovery } from "@/lib/discoveryStreamApi";

const API_BASE = "http://127.0.0.1:8000";

function sseBody(events: object[]): ReadableStream<Uint8Array> {
  const text = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
}

describe("streamDiscovery", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
    });
    vi.stubGlobal("window", { setTimeout, clearTimeout });
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", API_BASE);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("dispatches connected stage before later stages", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        sseBody([
          { type: "stage", stage: "connected", label: "连接已建立…" },
          { type: "stage", stage: "sector_heat", label: "计算板块热度…" },
          {
            type: "done",
            report_id: "r1",
            report: { id: "r1", title: "t", recommendations: [] },
          },
        ]),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const stages: string[] = [];
    await streamDiscovery(
      [{ fund_code: "519674", fund_name: "测试", holding_amount: 1000, return_percent: 1 }],
      {
        style: "稳健",
        horizon: "半年",
        max_drawdown_percent: 8,
        concentration_limit_percent: 35,
        expected_investment_amount: 10000,
        prefer_dca: true,
        avoid_chasing: true,
        decision_style: "conservative",
      },
      {
        onStage: (stage) => stages.push(stage),
        onDone: () => undefined,
      },
    );

    expect(stages[0]).toBe("connected");
    expect(stages).toContain("sector_heat");
    const requestBody = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.body),
    );
    expect(requestBody.discovery_strategy).toBe("opportunity_first");
    expect(requestBody.analysis_mode).toBe("deep");
  });

  it("throws when an active discovery stream stops sending progress events", async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                `data: ${JSON.stringify({
                  type: "stage",
                  stage: "news",
                  label: "news fetching",
                })}\n\n`,
              ),
            );
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const pending = streamDiscovery(
      [{ fund_code: "519674", fund_name: "test", holding_amount: 1000, return_percent: 1 }],
      {
        style: "steady",
        horizon: "half-year",
        max_drawdown_percent: 8,
        concentration_limit_percent: 35,
        expected_investment_amount: 10000,
        prefer_dca: true,
        avoid_chasing: true,
        decision_style: "conservative",
      },
      {},
      { idleTimeoutMs: 50 },
    );
    const expectation = expect(pending).rejects.toThrow(/长时间没有收到进度更新/);

    await vi.advanceTimersByTimeAsync(60);

    await expectation;
  });
});
