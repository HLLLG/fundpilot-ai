# 日报/推荐报告管线提速 · 阶段 3 实施计划

> **Status:** 2026-06-25 已落地

**Goal:** 在阶段 2 流式骨架基础上，实现 F5 竞品 takeaway 中的思考侧栏、可取消、可离开 + 通知 + 红点。

**Spec:** `docs/superpowers/specs/2026-06-25-report-pipeline-speedup-phase3-design.md`

---

## 任务清单

- [x] `streamingStageMeta.ts` — 阶段顺序 / 卡片状态 / thinking note
- [x] `ReportThinkingSidebar` — 右侧分析过程 + 输出摘要
- [x] `ReportSkeleton` — 双栏布局、停止生成、离开提示
- [x] `StreamingAnalysisFloat` — 非日报 Tab 时底部浮层
- [x] `Dashboard` — AbortController、完成不抢焦点、badge
- [x] `DashboardNav` — 日报 Tab 红点
- [x] `streamApi` — AbortError 传播
- [x] vitest：`streamingStageMeta` / `ReportPanel` 扩展用例
- [x] smoke 脚本：`sys.path` 自举 + 首只持仓 partial 单独计时

## 验证

```bash
cd apps/web && npm run typecheck && npm test -- src/lib/streamingStageMeta.test.ts src/components/ReportPanel.test.tsx
```
