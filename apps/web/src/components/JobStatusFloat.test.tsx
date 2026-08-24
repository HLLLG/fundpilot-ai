// @vitest-environment jsdom

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalysisJob, Report } from "@/lib/api";
import { fetchAnalysisJob, fetchReportDetail } from "@/lib/api";
import { JobStatusFloat } from "@/components/JobStatusFloat";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchAnalysisJob: vi.fn(),
    fetchReportDetail: vi.fn(),
  };
});

const fetchJob = vi.mocked(fetchAnalysisJob);
const fetchDetail = vi.mocked(fetchReportDetail);

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
    fetchDetail.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("hydrates the completed report by id instead of trusting an embedded body", async () => {
    const onComplete = vi.fn();
    const full = sampleReport();
    fetchJob.mockResolvedValue(
      job({
        status: "completed",
        report_id: "report-1",
      }),
    );
    fetchDetail.mockResolvedValue(full);

    render(
      <JobStatusFloat
        jobId="job-1"
        onComplete={onComplete}
        onClose={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(full));
    expect(fetchDetail).toHaveBeenCalledWith("report-1");
  });

  it("emits page-chart progress as the job stage advances", async () => {
    const onProgress = vi.fn();
    fetchJob.mockResolvedValue(
      job({
        stage: "generating",
        stage_label: "正在生成 AI 日报…",
      }),
    );

    render(
      <JobStatusFloat
        jobId="job-1"
        onComplete={vi.fn()}
        onClose={vi.fn()}
        onRetry={vi.fn()}
        onProgress={onProgress}
      />,
    );

    await waitFor(() =>
      expect(onProgress).toHaveBeenCalledWith({
        stage: "generating",
        stageLabel: "正在生成 AI 日报…",
        status: "running",
      }),
    );
  });
});
