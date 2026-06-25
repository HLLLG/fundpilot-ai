import { describe, expect, it } from "vitest";
import { appendStreamTokenBuffer, STREAM_TOKEN_BUFFER_MAX } from "@/lib/streamApi";
import { formatThinkingNote, stageCardStatus, stageShortLabel } from "@/lib/streamingStageMeta";

describe("streamingStageMeta", () => {
  it("maps stage short labels", () => {
    expect(stageShortLabel("fund_data")).toBe("净值与诊断");
    expect(stageShortLabel("generating")).toBe("AI 分析");
  });

  it("computes stage card status from completed set", () => {
    const completed = new Set(["fund_data", "news_prefetch"]);
    expect(stageCardStatus("fund_data", "news_summarize", completed)).toBe("done");
    expect(stageCardStatus("news_summarize", "news_summarize", completed)).toBe("active");
    expect(stageCardStatus("generating", "news_summarize", completed)).toBe("pending");
  });

  it("formats thinking notes for partial fields", () => {
    expect(formatThinkingNote("title", "持仓盘点")).toBe("已生成标题：持仓盘点");
    expect(
      formatThinkingNote("fund_recommendation", {
        fund_code: "519674",
        fund_name: "银河创新",
        action: "观察",
      }),
    ).toBe("已完成 银河创新 → 观察");
  });
});

describe("appendStreamTokenBuffer", () => {
  it("truncates to max length", () => {
    const chunk = "x".repeat(STREAM_TOKEN_BUFFER_MAX + 100);
    const result = appendStreamTokenBuffer("head", chunk);
    expect(result.length).toBe(STREAM_TOKEN_BUFFER_MAX);
    expect(result.endsWith("x")).toBe(true);
  });
});
