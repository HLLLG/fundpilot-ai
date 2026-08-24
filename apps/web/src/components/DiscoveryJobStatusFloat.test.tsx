// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import type { AnalysisJob, FundDiscoveryReport } from "@/lib/api";
import { fetchDiscoveryJob, fetchDiscoveryReportDetail } from "@/lib/api";
import { DiscoveryJobStatusFloat } from "@/components/DiscoveryJobStatusFloat";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchDiscoveryJob: vi.fn(),
    fetchDiscoveryReportDetail: vi.fn(),
  };
});

const fetchJob = vi.mocked(fetchDiscoveryJob);
const fetchDetail = vi.mocked(fetchDiscoveryReportDetail);

function sampleReport(): FundDiscoveryReport {
  return {
    id: "discovery-1",
    created_at: "2026-08-17T07:00:00Z",
    title: "机会报告",
    summary: "正文",
    focus_sectors: [],
    target_sectors: ["半导体"],
    recommendations: [
      { fund_code: "000001", fund_name: "测试", sector_name: "半导体", action: "建议关注" },
    ],
    caveats: [],
    provider: "test",
  };
}

function job(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    id: "job-1",
    status: "running",
    job_kind: "discovery",
    created_at: "2026-08-17T07:00:00Z",
    updated_at: "2026-08-17T07:00:00Z",
    stage_label: "正在优选候选基金…",
    ...overrides,
  };
}

describe("DiscoveryJobStatusFloat", () => {
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
        discovery_report_id: "discovery-1",
      }),
    );
    fetchDetail.mockResolvedValue(full);

    render(
      <DiscoveryJobStatusFloat
        jobId="job-1"
        onComplete={onComplete}
        onClose={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(full));
    expect(fetchDetail).toHaveBeenCalledWith("discovery-1");
    expect(onComplete.mock.calls[0]?.[0].recommendations).toHaveLength(1);
  });

  it("emits page-chart progress as the job stage advances", async () => {
    const onProgress = vi.fn();
    fetchJob.mockResolvedValue(
      job({
        stage: "candidate_pool",
        stage_label: "构建候选基金池…",
      }),
    );

    render(
      <DiscoveryJobStatusFloat
        jobId="job-1"
        onComplete={vi.fn()}
        onClose={vi.fn()}
        onRetry={vi.fn()}
        onProgress={onProgress}
      />,
    );

    await waitFor(() =>
      expect(onProgress).toHaveBeenCalledWith({
        stage: "candidate_pool",
        stageLabel: "构建候选基金池…",
        status: "running",
      }),
    );
  });

  it("marks the current stage failed when the job dies", async () => {
    const onProgress = vi.fn();
    fetchJob.mockResolvedValue(
      job({
        status: "failed",
        stage: "generating",
        error: "模型超时",
      }),
    );

    render(
      <DiscoveryJobStatusFloat
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
        stageLabel: "模型超时",
        status: "failed",
        error: "模型超时",
      }),
    );
    expect(screen.getByText("扫描失败")).toBeInTheDocument();
  });

  it("keeps polling when the page chart is visible and hides the float card", async () => {
    const onComplete = vi.fn();
    fetchJob.mockResolvedValue(
      job({
        status: "completed",
        discovery_report_id: "discovery-1",
      }),
    );
    fetchDetail.mockResolvedValue(sampleReport());

    render(
      <DiscoveryJobStatusFloat
        jobId="job-1"
        hideCard
        onComplete={onComplete}
        onClose={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("discovery-job-float")).not.toBeInTheDocument();
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
  });
});
