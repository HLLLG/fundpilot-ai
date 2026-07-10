import { describe, expect, it } from "vitest";

import { SITE_METADATA } from "@/lib/siteMetadata";


describe("SITE_METADATA", () => {
  it("publishes Lingxi metadata on hllingxi.cn", () => {
    expect(SITE_METADATA.title).toBe("灵析 | AI 基金研究台");
    expect(SITE_METADATA.metadataBase?.toString()).toBe("https://hllingxi.cn/");
    expect(SITE_METADATA.alternates?.canonical).toBe("/");
    expect(SITE_METADATA.openGraph).toMatchObject({
      siteName: "灵析 AI 基金研究台",
      url: "/",
    });
  });
});
