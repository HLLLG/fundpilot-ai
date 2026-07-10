import { describe, expect, it } from "vitest";

import { BRAND } from "@/lib/brand";


describe("BRAND", () => {
  it("defines the Lingxi public identity and production domain", () => {
    expect(BRAND.name).toBe("灵析");
    expect(BRAND.englishName).toBe("LINGXI");
    expect(BRAND.productName).toBe("灵析 AI 基金研究台");
    expect(BRAND.siteUrl).toBe("https://hllingxi.cn");
  });
});
