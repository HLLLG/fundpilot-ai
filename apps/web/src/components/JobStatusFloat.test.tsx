// @vitest-environment jsdom

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalysisJob, Report } from "@/lib/api";
import { fetchAnalysisJob } from "@/lib/api";
import { JobStatusFloat } from "@/components/JobStatusFloat";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchAnalysisJob: vi.fn(),
  };
});

const fetchJob = vi.mocked(fetchAnalysisJob);

function sampleReport(): Report {
  return {
    id: "report-1",
    created_at: "2026-08-17T07:00:00Z",
    title: "今日操作建议",
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

function job(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    id: "job-1",
    status: "running",
    created_at: "2026-08-17T07:00:00Z",
    updated_at: "2026-08-17T07:00:00Z",
    stage_label: "AI 分析中…",
    ...overrides,
  };
}

describe("JobStatusFloat", () => {
  beforeEach(() => {
    fetchJob.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("loads the finished report without waiting for a view click", async () => {
    const onComplete = vi.fn();
    fetchJob.mockResolvedValue(
      job({
        status: "completed",
        report: sampleReport(),
      }),
    );

    render(
      <JobStatusFloat
        jobId="job-1"
        onComplete={onComplete}
        onClose={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1));
    expect(onComplete.mock.calls[0]?.[0]).toMatchObject({ id: "report-1" });
    expect(document.querySelector('[data-testid="analysis-job-float"]')?.textContent).not.toContain(
      "查看报告",
    );
  });
});
