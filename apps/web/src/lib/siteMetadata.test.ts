import { existsSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { SITE_METADATA } from "@/lib/siteMetadata";


function pngDimensions(relativePath: string): { width: number; height: number } {
  const data = readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)));
  expect(data.subarray(1, 4).toString("ascii")).toBe("PNG");
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20) };
}


describe("SITE_METADATA", () => {
  it("publishes the registered website identity on www.hllingxi.cn", () => {
    expect(SITE_METADATA.title).toBe("数据分析学习笔记");
    expect(SITE_METADATA.metadataBase?.toString()).toBe("https://www.hllingxi.cn/");
    expect(SITE_METADATA.alternates?.canonical).toBe("/");
    expect(SITE_METADATA.openGraph).toMatchObject({
      siteName: "数据分析学习笔记",
      url: "/",
      images: [
        {
          url: "/social-card.jpg",
          width: 512,
          height: 512,
        },
      ],
    });
    expect(SITE_METADATA.twitter).toMatchObject({
      card: "summary",
      images: ["/social-card.jpg"],
    });
    expect(SITE_METADATA.icons).toMatchObject({
      icon: [{ url: "/icon.png", type: "image/png", sizes: "64x64" }],
      apple: [{ url: "/apple-icon.png", type: "image/png", sizes: "180x180" }],
    });
  });

  it("keeps generated app icons square and at platform-native sizes", () => {
    expect(pngDimensions("../app/icon.png")).toEqual({ width: 64, height: 64 });
    expect(pngDimensions("../app/apple-icon.png")).toEqual({ width: 180, height: 180 });
  });

  it("uses a compact dedicated social image instead of the browser favicon", () => {
    const socialImage = fileURLToPath(new URL("../../public/social-card.jpg", import.meta.url));
    const data = readFileSync(socialImage);
    expect(data.subarray(0, 2)).toEqual(Buffer.from([0xff, 0xd8]));
    expect(statSync(socialImage).size).toBeLessThan(50_000);
  });

  it("does not keep public icon copies shadowed by App Router metadata routes", () => {
    for (const file of ["../../public/icon.png", "../../public/icon.svg"]) {
      expect(existsSync(fileURLToPath(new URL(file, import.meta.url)))).toBe(false);
    }
    expect(existsSync(fileURLToPath(new URL("../app/icon.svg", import.meta.url)))).toBe(false);
  });
});
