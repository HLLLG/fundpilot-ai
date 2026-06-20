# 好基灵 toC 改造 — 第 0+1 批 实现计划

> **For agentic workers:** 本计划为前端表现层改造（Next.js 16 / React 19 / Tailwind v4）。多为纯展示性改动，按任务逐步实现；每个任务后跑 `lint / typecheck` 验证，全部完成后跑 `build`。步骤用 `- [ ]` 跟踪。

**Goal:** 把好基灵从"功能工具"升级为有营销门面、亲和好看、有安全感的 toC 产品第一印象（设计地基 + 落地页 + 登录注册 + 持有页空状态）。

**Architecture:** 先在 `globals.css` 铺设计 token 与共用组件类（地基）；再新增登录前品牌落地页并改造 `Home` 路由分支（未登录→落地页，已登录→Dashboard）；统一登录/注册视觉；重做 Dashboard 顶部品牌头与持有页空状态。纯前端，不动后端与数据流，不破坏静态导出。

**Tech Stack:** Next.js 16 (output: export)、React 19、Tailwind v4、lucide-react、Plus Jakarta Sans。

参考设计方案：`docs/superpowers/specs/2026-06-20-haojiling-toc-redesign-design.md`

---

## 文件结构

- 修改 `apps/web/src/app/globals.css` — 设计 token + 共用组件类（地基）
- 修改 `apps/web/src/app/layout.tsx` — 站点 metadata 改为「好基灵」
- 新建 `apps/web/src/components/LandingPage.tsx` — 登录前品牌落地页
- 新建 `apps/web/src/components/BrandMark.tsx` — 复用的 Logo + 好基灵 品牌标识
- 修改 `apps/web/src/components/AuthProvider.tsx` — `/` 纳入公开可访问（未登录不强制跳登录）
- 修改 `apps/web/src/app/page.tsx` — 按登录态分支渲染 Landing / Dashboard
- 修改 `apps/web/src/app/login/page.tsx` — 统一新设计语言 + 品牌头
- 修改 `apps/web/src/app/register/page.tsx` — 统一新设计语言 + 品牌头
- 修改 `apps/web/src/components/Dashboard.tsx` — 顶部品牌头用 BrandMark（好基灵）
- 修改 `apps/web/src/components/YangjibaoHoldingsBoard.tsx` — 持有页空状态友好引导

---

## Task 0：设计地基（globals.css）

**Files:** Modify `apps/web/src/app/globals.css`

- [ ] 扩展 `:root` token：新增 `--brand-strong`、`--accent`、`--accent-soft`、`--radius-card`、`--shadow-sm/md/lg`；更新 `--brand` → `#2563EB`、`--background` → `#F6F8FB`、`--foreground` → `#0F172A`、`--muted` → `#64748B`；保留 `--profit-up`/`--profit-down` 不变。
- [ ] 新增共用组件类：`.btn-primary` `.btn-secondary` `.btn-ghost` `.btn-accent`（统一高度/圆角/hover/active/disabled）、`.badge` 系列、`.empty-state`、卡片 hover 抬升 `.card-hover`、落地页用 `.landing-*` 辅助类。
- [ ] 升级 `.section-card`（圆角 20px、分层阴影）、保留旧类名兼容。
- [ ] 验证：`npm run lint` 通过（CSS 不报错），`npm run typecheck` 不受影响。

## Task 1：站点 metadata + BrandMark

**Files:** Modify `apps/web/src/app/layout.tsx`；Create `apps/web/src/components/BrandMark.tsx`

- [ ] `layout.tsx` 的 `metadata.title` 改为 `好基灵 | 截个图就懂你的基金`，`description` 改为副标语。
- [ ] 新建 `BrandMark`：props `{ size?: "sm" | "md" | "lg"; showName?: boolean }`，渲染品牌图标（lucide `Sparkles`/`BrainCircuit` 置于品牌蓝圆角块）+「好基灵」中文名 + 可选英文 `FundPilot` 小字。
- [ ] 验证：`npm run typecheck` 通过。

## Task 2：落地页 LandingPage

**Files:** Create `apps/web/src/components/LandingPage.tsx`

- [ ] 实现登录前落地页：品牌头（BrandMark + 登录/注册按钮）→ 主视觉（大标题「好基灵，截个图就懂你的基金」+ 副标语 + 主 CTA「免费注册」`Link href=/register` + 次 CTA「已有账号登录」`Link href=/login`）→ 三能力卡（拍图识别持仓 / 实时追踪板块冷暖 / 听得懂的投研日报，用 lucide 图标）→「为什么放心用」区块（本地优先、不上传原始截图、隐私边界）→ 底部风险提示 + 版权。
- [ ] 响应式：移动端单列、桌面端多列；用 Task 0 的 token 与组件类。
- [ ] 验证：`npm run typecheck` 通过。

## Task 3：路由分支（AuthProvider + Home）

**Files:** Modify `apps/web/src/components/AuthProvider.tsx`、`apps/web/src/app/page.tsx`

- [ ] `AuthProvider`：将 `/` 视为公开路径之一——未登录在 `/` 不再 `router.replace("/login")`；其余受保护逻辑不变；已登录访问 `/login`/`/register` 仍跳 `/`。
- [ ] `page.tsx`：改为 `"use client"`，`const { user } = useAuth()`；`user ? <Dashboard/> : <LandingPage/>`。
- [ ] 验证：未登录访问 `/` 显示落地页、不再被踢登录；登录后显示 Dashboard；登录态访问 `/login` 跳回 `/`。`npm run typecheck` 通过。

## Task 4：登录 / 注册页统一视觉

**Files:** Modify `apps/web/src/app/login/page.tsx`、`apps/web/src/app/register/page.tsx`

- [ ] 顶部加 BrandMark；卡片、输入框、按钮统一到新 token / 组件类；保留所有表单字段与提交逻辑不变；加返回落地页链接。
- [ ] 验证：登录、注册流程功能不变；`npm run typecheck`、`npm run lint` 通过。

## Task 5：Dashboard 顶部品牌头

**Files:** Modify `apps/web/src/components/Dashboard.tsx`

- [ ] 顶部 `nav` 用 `BrandMark`（显示「好基灵」中文名）替换当前 `FundPilot` 文本块；UserMenu 不变。
- [ ] 验证：`npm run typecheck`、`npm run lint` 通过。

## Task 6：持有页空状态

**Files:** Modify `apps/web/src/components/YangjibaoHoldingsBoard.tsx`

- [ ] 当 `holdings` 为空且非 loading 时，渲染友好空状态（图标 + 标题「截张图，30 秒看懂你的基金」+ 说明 + 「上传截图」主按钮触发 `onAddHolding`）。先读现有组件确认 loading/空判断与 props，再改。
- [ ] 验证：空状态展示正常，有持仓时不受影响；`npm run typecheck`、`npm run lint` 通过。

## Task 7：整体验收

- [ ] `npm run lint`、`npm run typecheck`、`npm run build` 全绿。
- [ ] `npm run test`（vitest）现有单测不回归。
- [ ] 用户本地刷新预览第 1 批效果。

---

## Self-Review

- 覆盖 spec 第 1 批四项（落地页 / 登录注册 / 品牌头 / 持有空状态）+ 第 0 批地基：✓
- 路由改法与 spec 第 3 节一致：✓
- 不动后端、不破坏静态导出、涨跌色不变：✓
- 无占位符；每个任务有明确文件与验证命令。
