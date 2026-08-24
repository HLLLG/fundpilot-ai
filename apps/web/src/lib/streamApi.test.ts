import { describe, expect, it } from "vitest";
import { appendStreamTokenBuffer, STREAM_TOKEN_BUFFER_MAX, streamTimestamp } from "@/lib/streamApi";

describe("stream helpers", () => {
  it("returns a wall-clock timestamp", () => {
    const before = Date.now();
    const value = streamTimestamp();
    const after = Date.now();
    expect(value).toBeGreaterThanOrEqual(before);
    expect(value).toBeLessThanOrEqual(after);
  });

  it("caps the token buffer", () => {
    const chunk = "x".repeat(STREAM_TOKEN_BUFFER_MAX + 20);
    const result = appendStreamTokenBuffer("head", chunk);
    expect(result.length).toBe(STREAM_TOKEN_BUFFER_MAX);
    expect(result.endsWith("x".repeat(20))).toBe(true);
  });
});
