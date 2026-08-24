import { describe, expect, it } from "vitest";

import {
  ANALYZE_BLOCKS_DISCOVERY,
  DISCOVERY_BLOCKS_ANALYZE,
} from "@/lib/researchStreamMutex";

describe("research stream mutex copy", () => {
  it("tells the user to wait for the other job", () => {
    expect(ANALYZE_BLOCKS_DISCOVERY).toContain("日报正在生成");
    expect(DISCOVERY_BLOCKS_ANALYZE).toContain("发现基金正在扫描");
  });
});
