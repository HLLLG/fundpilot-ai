import { describe, expect, it } from "vitest";
import {
  DISCOVERY_SCAN_STEPS,
  discoveryScanStepIndex,
  resolveDiscoveryScanTrack,
} from "@/lib/discoveryScanProgress";

describe("discoveryScanStepIndex", () => {
  it("maps backend stages onto the visible chart", () => {
    expect(discoveryScanStepIndex("queued")).toBe(0);
    expect(discoveryScanStepIndex("connected")).toBe(0);
    expect(discoveryScanStepIndex("candidate_pool")).toBe(2);
    expect(discoveryScanStepIndex("fetch_market_news")).toBe(3);
    expect(discoveryScanStepIndex("tool_round_2")).toBe(3);
    expect(discoveryScanStepIndex("salvage")).toBe(4);
    expect(discoveryScanStepIndex("completed")).toBe(DISCOVERY_SCAN_STEPS.length);
  });
});

describe("resolveDiscoveryScanTrack", () => {
  it("keeps unreached nodes pending and lights the current station", () => {
    const track = resolveDiscoveryScanTrack({
      stage: "candidate_pool",
      stageLabel: "构建候选基金池…",
      status: "running",
    });

    expect(track.nodes.map((node) => node.state)).toEqual([
      "done",
      "done",
      "current",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
    expect(track.reachedCount).toBe(3);
    expect(track.total).toBe(7);
    expect(track.fillPercent).toBeCloseTo((2 / 6) * 100);
    expect(track.headline).toBe("构建候选基金池…");
  });

  it("marks the broken station with a failed state and leaves later nodes gray", () => {
    const track = resolveDiscoveryScanTrack({
      stage: "generating",
      stageLabel: "AI 分析中…",
      status: "failed",
      error: "模型超时",
    });

    expect(track.nodes.map((node) => node.state)).toEqual([
      "done",
      "done",
      "done",
      "done",
      "failed",
      "pending",
      "pending",
    ]);
    expect(track.headline).toBe("模型超时");
  });

  it("fills the whole chart when the job completes", () => {
    const track = resolveDiscoveryScanTrack({
      stage: "completed",
      stageLabel: "完成",
      status: "completed",
    });

    expect(track.nodes.every((node) => node.state === "done")).toBe(true);
    expect(track.fillPercent).toBe(100);
    expect(track.reachedCount).toBe(7);
  });
});
