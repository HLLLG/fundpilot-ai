// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { Report } from "@/lib/api";
import { streamAnalysis } from "@/lib/streamApi";

afterEach(() => {
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

describe("streamAnalysis", () => {
  it("dispatches stage, skeleton, partial, and done events", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: sseBody([
        { type: "stage", stage: "fund_data", label: "拉取净值" },
        { type: "skeleton", fund_codes: ["519674"], fund_names: ["银河"] },
        { type: "token", content: '{"title":' },
        { type: "token", content: '"t"' },
        {
          type: "report_partial",
          field: "fund_recommendation",
          value: { fund_code: "519674", action: "观察", points: [] },
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
    ).rejects.toThrow(/流式连接未收到事件/);
  });
});
