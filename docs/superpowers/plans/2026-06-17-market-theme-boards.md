# 市场 Tab — 主题板块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在市场 Tab 增加「主题板块」子 Tab，展示 canonical 19 主题的养基宝式表格（日涨幅、连涨天数、关联基金数、我的持仓）。

**Architecture:** 新建 `theme_board_snapshot.py` 拉东财日 K 算连涨天数，关联基金数来自 seeds + `fund_primary_sectors`；`GET /api/market/theme-boards` 缓存不含用户维度，响应时叠加持仓。前端 `MarketTab` 增加子 Tab，`ThemeSectorOverview` 表格组件。

**Tech Stack:** FastAPI, pytest, Next.js, TypeScript, Tailwind, `useCachedFetch`

**Spec:** `docs/superpowers/specs/2026-06-17-market-theme-boards-design.md`

---

### Task 1: 连涨天数纯函数 + 单测

**Files:**
- Create: `apps/api/tests/test_theme_board_snapshot.py`
- Create: `apps/api/app/services/theme_board_snapshot.py`（仅 `compute_consecutive_up_days`）

- [ ] RED: 写 `test_compute_consecutive_up_days_*`
- [ ] GREEN: 实现 `compute_consecutive_up_days`
- [ ] 运行: `pytest apps/api/tests/test_theme_board_snapshot.py -q`

### Task 2: 主题行构建 + 缓存 + API

**Files:**
- Modify: `apps/api/app/services/theme_board_snapshot.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/conftest.py`（stub `get_theme_board_snapshot`）
- Modify: `apps/api/tests/test_api.py`

- [ ] 实现 `build_linked_fund_counts`、`get_theme_board_snapshot`、`build_theme_board_payload`
- [ ] 路由 `GET /api/market/theme-boards`
- [ ] API smoke 测试

### Task 3: 前端

**Files:**
- Create: `apps/web/src/components/ThemeSectorOverview.tsx`
- Create: `apps/web/src/lib/marketThemeBoard.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/components/MarketTab.tsx`

- [ ] 类型 + `fetchMarketThemeBoards`
- [ ] 子 Tab + 表格组件
- [ ] `npm run lint && npm run typecheck && npm run build`

### Task 4: 文档

- [ ] 更新 `docs/PROJECT_CONTEXT.md` 能力清单与 API 表
- [ ] Spec 状态改为已实现
