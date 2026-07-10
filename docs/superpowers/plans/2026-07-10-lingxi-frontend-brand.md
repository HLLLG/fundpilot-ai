# 灵析前端品牌替换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将前端用户可见品牌统一为“灵析 / LINGXI”，并把 `https://hllingxi.cn` 写入正式站点元数据。

**Architecture:** 新增纯常量品牌模块与独立站点元数据模块，组件只引用品牌模块，根布局只引用元数据模块。内部 `fundpilot-*` 存储键和基础设施标识不改，以保持已有会话、缓存和部署兼容。

**Tech Stack:** Next.js 16、React 19、TypeScript、Vitest、Testing Library

---

### Task 1: 建立品牌唯一来源

**Files:**
- Create: `apps/web/src/lib/brand.test.ts`
- Create: `apps/web/src/lib/brand.ts`

- [ ] **Step 1: 写品牌常量失败测试**

```ts
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
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `npm test -- src/lib/brand.test.ts`

Expected: FAIL，提示无法解析 `@/lib/brand`。

- [ ] **Step 3: 添加最小品牌常量**

```ts
export const BRAND = {
  name: "灵析",
  englishName: "LINGXI",
  productName: "灵析 AI 基金研究台",
  siteUrl: "https://hllingxi.cn",
  title: "灵析 | AI 基金研究台",
  description:
    "灵析：智能识别基金持仓，追踪市场与板块变化，结合量化证据生成个性化投研分析与风险提示。",
} as const;
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `npm test -- src/lib/brand.test.ts`

Expected: 1 test passed。

### Task 2: 统一站点元数据与品牌标识

**Files:**
- Create: `apps/web/src/lib/siteMetadata.test.ts`
- Create: `apps/web/src/lib/siteMetadata.ts`
- Modify: `apps/web/src/app/layout.tsx`
- Create: `apps/web/src/components/BrandMark.test.tsx`
- Modify: `apps/web/src/components/BrandMark.tsx`

- [ ] **Step 1: 写元数据和品牌标识失败测试**

```ts
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
```

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { BrandMark } from "@/components/BrandMark";

afterEach(cleanup);

describe("BrandMark", () => {
  it("renders the Lingxi Chinese and English names", () => {
    render(<BrandMark showEnglish />);
    expect(screen.getByText("灵析")).toBeInTheDocument();
    expect(screen.getByText("LINGXI")).toBeInTheDocument();
    expect(screen.queryByText("好基灵")).not.toBeInTheDocument();
    expect(screen.queryByText("FundPilot")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试并确认分别因缺少元数据模块与旧品牌显示而失败**

Run: `npm test -- src/lib/siteMetadata.test.ts src/components/BrandMark.test.tsx`

Expected: FAIL，失败原因与待实现品牌替换一致。

- [ ] **Step 3: 实现站点元数据并接入根布局**

`siteMetadata.ts` 导出 Next.js `Metadata`，使用 `BRAND.siteUrl` 创建 `metadataBase`，设置 canonical `/`、Open Graph 站点名与 URL、中文 locale 和 summary large image Twitter 卡片。`layout.tsx` 删除内联元数据，改为 `export { SITE_METADATA as metadata } from "@/lib/siteMetadata"`。

- [ ] **Step 4: 更新 BrandMark**

从 `@/lib/brand` 导入 `BRAND`，中文显示 `BRAND.name`，英文辅助显示 `BRAND.englishName`，同步更新组件注释。

- [ ] **Step 5: 运行两个测试并确认通过**

Run: `npm test -- src/lib/siteMetadata.test.ts src/components/BrandMark.test.tsx`

Expected: 2 tests passed。

### Task 3: 替换其余用户可见品牌文案

**Files:**
- Modify: `apps/web/src/components/LandingPage.tsx`
- Modify: `apps/web/src/components/AddHoldingModal.tsx`
- Modify: `apps/web/src/components/Dashboard.tsx`
- Modify: `apps/web/src/components/PortfolioFactorScoresPanel.tsx`
- Modify: `apps/web/src/components/PortfolioRiskMetricsPanel.tsx`
- Modify: `apps/web/src/components/YangjibaoHoldingsBoard.tsx`
- Modify: `apps/web/src/app/globals.css`

- [ ] **Step 1: 让可见文案引用品牌常量**

将落地页、上传提示、桌面通知、Pro 解锁提示和持仓说明中的“好基灵 / FundPilot”替换为 `BRAND.name` 或 `BRAND.englishName`。落地页眉品牌定位改为“AI 基金研究台”，页脚改为 `灵析 LINGXI`。CSS 注释同步改为“灵析设计语言”。

- [ ] **Step 2: 验证前端源文件无旧的可见品牌文案**

Run: `rg -n "好基灵|FundPilot" apps/web/src`

Expected: 无匹配。`fundpilot-*` 小写内部兼容键允许继续存在。

- [ ] **Step 3: 运行品牌测试和全量单元测试**

Run: `npm test -- src/lib/brand.test.ts src/lib/siteMetadata.test.ts src/components/BrandMark.test.tsx`

Expected: 3 tests passed。

Run: `npm test`

Expected: 所有测试通过。

### Task 4: 完整前端验证

**Files:**
- Verify only

- [ ] **Step 1: 类型检查**

Run: `npm run typecheck`

Expected: exit 0。

- [ ] **Step 2: Lint**

Run: `npm run lint`

Expected: exit 0，0 warnings。

- [ ] **Step 3: 生产构建**

Run: `npm run build`

Expected: exit 0，静态导出成功。

- [ ] **Step 4: 复查差异**

Run: `git diff --check && git status --short && git diff --stat`

Expected: 无空白错误，变更仅包含品牌配置、元数据、用户可见前端文案、测试和本计划。
