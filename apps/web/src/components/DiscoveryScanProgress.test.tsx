// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { DiscoveryScanProgress } from "@/components/DiscoveryScanProgress";

afterEach(() => {
  cleanup();
});

describe("DiscoveryScanProgress", () => {
  it("renders the whole voyage and lights only the current station", () => {
    render(
      <DiscoveryScanProgress
        progress={{
          stage: "news",
          stageLabel: "拉取市场要闻…",
          status: "running",
        }}
      />,
    );

    expect(screen.getByTestId("discovery-scan-progress")).toHaveAttribute("data-status", "running");
    expect(screen.getByText("拉取市场要闻…")).toBeInTheDocument();
    expect(screen.getByText("04")).toBeInTheDocument();
    expect(screen.getByTestId("discovery-scan-step-news")).toHaveAttribute("data-state", "current");
    expect(screen.getByTestId("discovery-scan-step-guarding")).toHaveAttribute("data-state", "pending");
  });

  it("puts a failed mark on the broken station", () => {
    render(
      <DiscoveryScanProgress
        progress={{
          stage: "saving",
          stageLabel: "保存报告…",
          status: "failed",
          error: "写入失败",
        }}
      />,
    );

    expect(screen.getByTestId("discovery-scan-step-saving")).toHaveAttribute("data-state", "failed");
    expect(screen.getByText("写入失败")).toBeInTheDocument();
    expect(screen.getByText("出错节点已标红，可重试本轮扫描。")).toBeInTheDocument();
  });
});
