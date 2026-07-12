// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { DiscoveryJobStatusFloat } from "@/components/DiscoveryJobStatusFloat";
import { DiscoveryStreamingFloat } from "@/components/DiscoveryStreamingFloat";
import { ReportSkeleton } from "@/components/ReportSkeleton";
import { StreamingAnalysisFloat } from "@/components/StreamingAnalysisFloat";
import { fetchDiscoveryJob } from "@/lib/api";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import type { StreamingReportState } from "@/lib/streamApi";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchDiscoveryJob: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const discoveryStreaming: StreamingDiscoveryState = {
  stage: "screening",
  stageLabel: "正在筛选候选基金",
  fundCodes: [],
  fundNames: [],
  partialByCode: {},
  stageLog: [],
  tokenBuffer: "",
  startedAt: Date.now(),
};

const reportStreaming: StreamingReportState = {
  stage: "fund_data",
  stageLabel: "正在读取基金数据",
  fundCodes: [],
  fundNames: [],
  partialByCode: {},
  stageLog: [],
  thinkingNotes: [],
  startedAt: Date.now(),
  tokenBuffer: "",
  sessionId: "session-1",
  followupNotes: [],
};

describe("remaining mobile touch targets", () => {
  it("keeps both streaming cancellation controls at least 44px square", () => {
    const { rerender } = render(
      <DiscoveryStreamingFloat
        streaming={discoveryStreaming}
        onOpenDiscovery={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "取消扫描" })).toHaveClass(
      "min-h-11",
      "min-w-11",
    );

    rerender(
      <StreamingAnalysisFloat
        streaming={reportStreaming}
        onOpenReport={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "取消分析" })).toHaveClass(
      "min-h-11",
      "min-w-11",
    );
  });

  it("keeps completed and failed discovery-job actions at least 44px tall", async () => {
    vi.mocked(fetchDiscoveryJob).mockResolvedValueOnce({
      id: "completed-job",
      status: "completed",
      created_at: "2026-07-11T00:00:00Z",
      updated_at: "2026-07-11T00:00:01Z",
      discovery_report: {
        id: "report-1",
        created_at: "2026-07-11T00:00:00Z",
        title: "示例报告",
        summary: "摘要",
        focus_sectors: [],
        target_sectors: [],
        recommendations: [],
        caveats: [],
        provider: "test",
      },
    });
    const { unmount } = render(
      <DiscoveryJobStatusFloat
        jobId="completed-job"
        onComplete={vi.fn()}
        onClose={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(await screen.findByRole("button", { name: "查看报告" })).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "关闭" })).toHaveClass("min-h-11");
    unmount();

    vi.mocked(fetchDiscoveryJob).mockResolvedValueOnce({
      id: "failed-job",
      status: "failed",
      created_at: "2026-07-11T00:00:00Z",
      updated_at: "2026-07-11T00:00:01Z",
      error: "扫描失败",
    });
    render(
      <DiscoveryJobStatusFloat
        jobId="failed-job"
        onComplete={vi.fn()}
        onClose={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(await screen.findByRole("button", { name: "重试" })).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "关闭" })).toHaveClass("min-h-11");
  });

  it("keeps report cancellation and follow-up submission at least 44px tall", () => {
    render(
      <ReportSkeleton
        streaming={reportStreaming}
        onCancel={vi.fn()}
        onFollowup={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "停止生成" })).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "发送补充" })).toHaveClass("min-h-11");
  });
});
