import { describe, expect, it } from "vitest";

import { metadata as loginMetadata } from "@/app/login/layout";
import { metadata as registerMetadata } from "@/app/register/layout";
import { metadata as settingsMetadata } from "@/app/settings/layout";

describe("account route metadata", () => {
  it.each([
    ["login", loginMetadata, "登录 | 数据分析学习笔记", "/login"],
    ["register", registerMetadata, "免费注册 | 数据分析学习笔记", "/register"],
    ["settings", settingsMetadata, "账号设置 | 数据分析学习笔记", "/settings"],
  ])("publishes descriptive, non-indexable metadata for %s", (_route, metadata, title, canonical) => {
    expect(metadata.title).toBe(title);
    expect(metadata.description).toBeTruthy();
    expect(metadata.alternates?.canonical).toBe(canonical);
    expect(metadata.robots).toMatchObject({
      index: false,
      follow: false,
    });
  });
});
