import { describe, expect, it } from "vitest";

import { BRAND } from "@/lib/brand";


describe("BRAND", () => {
  it("defines the registered public identity and production domain", () => {
    expect(BRAND.name).toBe("数据分析学习笔记");
    expect(BRAND.englishName).toBe("DATA ANALYSIS NOTES");
    expect(BRAND.productName).toBe("数据分析学习笔记");
    expect(BRAND.siteUrl).toBe("https://www.hllingxi.cn");
  });
});
