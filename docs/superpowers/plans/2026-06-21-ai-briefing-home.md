# AI 简报首页（方案 B）实现计划

> **For agentic workers:** 按任务逐步实现；每步后跑 `lint / typecheck / test`；全部完成后跑 `build`。

**Goal:** 登录后默认展示 AI 简报首页，持仓完整看板独立 Tab，移动端底部导航。

**Architecture:** 新增 `TodayBriefing` 作为 `today` Tab 内容；`holdings` Tab 承载原 `YangjibaoHoldingsBoard`；`DashboardNav` 负责桌面顶栏 + 移动底栏；`todayBriefing.ts` 提取日报摘要逻辑。

**Tech Stack:** Next.js 16, React 19, Tailwind v4, lucide-react, vitest

参考 spec：`docs/superpowers/specs/2026-06-21-ai-briefing-home-design.md`

---

## 文件清单

| 文件 | 动作 |
|------|------|
| `src/lib/todayBriefing.ts` | 新建 — 摘要/板块/Top持仓工具 |
| `src/lib/todayBriefing.test.ts` | 新建 — 单测 |
| `src/components/TodayBriefing.tsx` | 新建 — 简报首页 UI |
| `src/components/DashboardNav.tsx` | 新建 — 导航 |
| `src/components/Dashboard.tsx` | 修改 — Tab 分流 |
| `src/lib/storage.ts` | 修改 — 增加 `holdings` Tab |
| `src/app/globals.css` | 修改 — 简报 + 底栏样式 |

---

## Task 1: todayBriefing 工具层 ✅

- `findTodayReport` / `extractBriefingSummary` / `pickTopHoldings` / `resolveSectorPulse`

## Task 2: TodayBriefing 组件 ✅

- Hero KPI + AI 卡 + 板块脉搏 + 持仓快览 + 空状态

## Task 3: DashboardNav ✅

- 桌面 6 Tab；移动 4+更多

## Task 4: Dashboard 接入 ✅

- `today` → TodayBriefing；`holdings` → YangjibaoHoldingsBoard

## Task 5: 验证 ✅

```bash
cd apps/web && npm run lint && npm run typecheck && npm run test && npm run build
```
