// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { Report } from "@/lib/api";
import {
  markStreamingReportBackgroundFallback,
  streamAnalysis,
  streamTimestamp,
  type StreamingReportState,
} from "@/lib/streamApi";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function sseBody(events: object[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const payload = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(payload));
      controller.close();
    },
  });
}

function sampleReport(): Report {
  return {
    id: "r1",
    created_at: "2026-06-25T10:00:00Z",
    title: "t",
    summary: "s",
    risk: {
      level: "low",
      suggested_action: "watch",
      weighted_return_percent: 0,
      alerts: [],
    },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    fund_recommendations: [],
    recommendations: [],
    caveats: [],
    provider: "test",
  };
}

function testProfile(): import("@/lib/api").InvestorProfile {
  return {
    style: "conservative",
    horizon: "medium",
    max_drawdown_percent: 15,
    concentration_limit_percent: 30,
    expected_investment_amount: 100000,
    prefer_dca: true,
    avoid_chasing: true,
    decision_style: "conservative",
  };
}

function streamingState(): StreamingReportState {
  return {
    stage: "news_summarize",
    stageLabel: "正在生成主题要闻摘要...",
    fundCodes: ["519674"],
    fundNames: ["Galaxy"],
    partialByCode: {},
    stageLog: [{ stage: "news_summarize", label: "正在生成主题要闻摘要...", at: streamTimestamp() }],
    thinkingNotes: [],
    startedAt: streamTimestamp() - 10_000,
    tokenBuffer: "",
    followupNotes: [],
  };
}

describe("streamAnalysis", () => {
  it("dispatches stage, skeleton, partial, and done events", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: sseBody([
        { type: "stage", stage: "fund_data", label: "fund data" },
        { type: "skeleton", fund_codes: ["519674"], fund_names: ["Galaxy"] },
        { type: "token", content: '{"title":' },
        { type: "token", content: '"t"' },
        {
          type: "report_partial",
          field: "fund_recommendation",
          value: { fund_code: "519674", action: "watch", points: [] },
        },
        { type: "done", report_id: "r1", report: sampleReport() },
      ]),
    });
    vi.stubGlobal("fetch", fetchMock);

    const stages: string[] = [];
    const skeletons: string[][] = [];
    const partials: string[] = [];
    const tokens: string[] = [];
    let doneReport: Report | undefined;

    await streamAnalysis(
      [],
      testProfile(),
      {
        onStage: (stage) => stages.push(stage),
        onSkeleton: (codes) => skeletons.push(codes),
        onToken: (content) => tokens.push(content),
        onPartial: (field) => partials.push(field),
        onDone: (report) => {
          doneReport = report;
        },
      },
      { analysisMode: "fast" },
    );

    expect(stages).toEqual(["fund_data"]);
    expect(skeletons).toEqual([["519674"]]);
    expect(tokens).toEqual(['{"title":', '"t"']);
    expect(partials).toEqual(["fund_recommendation"]);
    expect(doneReport?.id).toBe("r1");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/analyze/stream"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws when stream ends without any SSE events", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: new ReadableStream({
        start(controller) {
          controller.close();
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      streamAnalysis([], testProfile(), {}, { analysisMode: "fast" }),
    ).rejects.toThrow();
  });

  it("throws when an active stream stops sending progress events", async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            encoder.encode(
              `data: ${JSON.stringify({
                type: "stage",
                stage: "news_summarize",
                label: "news summarizing",
              })}\n\n`,
            ),
          );
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const pending = streamAnalysis(
      [],
      testProfile(),
      {},
      { analysisMode: "deep", idleTimeoutMs: 50 },
    );
    const expectation = expect(pending).rejects.toThrow(/long time without progress/);

    await vi.advanceTimersByTimeAsync(60);

    await expectation;
  });
});

describe("streaming fallback state", () => {
  it("preserves the report skeleton state when switching to a background job", () => {
    const next = markStreamingReportBackgroundFallback(
      streamingState(),
      "job-1",
      "流式生成长时间没有进展",
    );

    expect(next?.backgroundJobId).toBe("job-1");
    expect(next?.fundCodes).toEqual(["519674"]);
    expect(next?.stage).toBe("news_summarize");
    expect(next?.stageLabel).toContain("后台分析");
    expect(next?.thinkingNotes.at(-1)).toContain("流式生成长时间没有进展");
  });
});
