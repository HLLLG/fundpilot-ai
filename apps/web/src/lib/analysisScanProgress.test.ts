import { describe, expect, it } from "vitest";
import { resolveAnalysisScanTrack } from "@/lib/analysisScanProgress";

describe("resolveAnalysisScanTrack", () => {
  it("lights the current daily-report station", () => {
    const track = resolveAnalysisScanTrack({
      stage: "generating",
      stageLabel: "正在生成 AI 日报…",
      status: "running",
    });

    expect(track.nodes.map((node) => node.state)).toEqual([
      "done",
      "done",
      "done",
      "done",
      "current",
      "pending",
      "pending",
    ]);
    expect(track.reachedCount).toBe(5);
    expect(track.headline).toBe("正在生成 AI 日报…");
  });

  it("maps tool rounds onto the news station", () => {
    const track = resolveAnalysisScanTrack({
      stage: "tool_round_2",
      stageLabel: "正在检索新闻 (2/3)…",
      status: "running",
    });
    expect(track.nodes[1]?.state).toBe("current");
    expect(track.nodes[1]?.id).toBe("news_prefetch");
  });

  it("marks the broken station failed", () => {
    const track = resolveAnalysisScanTrack({
      stage: "judging",
      stageLabel: "正在审校报告…",
      status: "failed",
      error: "审校超时",
    });
    expect(track.nodes.map((node) => node.state)).toEqual([
      "done",
      "done",
      "done",
      "done",
      "done",
      "failed",
      "pending",
    ]);
    expect(track.headline).toBe("审校超时");
  });
});
