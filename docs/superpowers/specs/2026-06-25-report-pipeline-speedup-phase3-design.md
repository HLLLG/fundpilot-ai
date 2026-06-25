# 日报/推荐报告管线提速 · 阶段 3 设计：流式 UI 精修

**版本：** 2026-06-25  
**前置：** 阶段 2（SSE 流式 + 骨架卡）已落地  
**目标：** 参考阶段 1 spec 附录 F5 竞品 takeaway，在阶段 2 骨架基础上补齐「思考过程可见、可离开、可取消、完成提醒」。

---

## 1. 范围

### 做

| 能力 | 说明 |
|---|---|
| 思考过程侧栏 | `ReportThinkingSidebar`：阶段卡片 stepper + 输出摘要（partial 事件转可读 note） |
| 阶段进度卡片化 | 6 步与后端 `JOB_STAGES` 对齐，done / active / pending 状态 |
| 可取消 | `AbortController` 中断 SSE；「停止生成」按钮 |
| 允许离开 | 切走日报 Tab 时 `StreamingAnalysisFloat` 浮层继续显示进度 |
| 浏览器通知 | 启动时 `ensureNotificationPermission`；完成时 `notifyDesktop`（沿用） |
| 日报 Tab 红点 | 用户离开日报 Tab 期间生成完成 → `reportTabUnread` badge |
| 不强制跳转 | 用户已离开日报 Tab 时，完成不 `setActiveTab('report')`，仅红点 + 通知 |

### 不做（留后续）

- 中途追加 prompt（F5 takeaway #4）
- token 级打字机光标（Notion 范式，可选）
- deep 模式流式
- 荐基 `/api/fund-discovery/stream`

---

## 2. 文件清单

| 路径 | 责任 |
|---|---|
| `apps/web/src/lib/streamingStageMeta.ts` | 阶段顺序、卡片状态、thinking note 格式化 |
| `apps/web/src/components/ReportThinkingSidebar.tsx` | 右侧分析过程侧栏 |
| `apps/web/src/components/StreamingAnalysisFloat.tsx` | 离开日报 Tab 时的紧凑浮层 |
| `apps/web/src/components/ReportSkeleton.tsx` | 双栏布局 + 取消 + 离开提示 |
| `apps/web/src/components/Dashboard.tsx` | abort、badge、完成时不抢焦点 |
| `apps/web/src/components/DashboardNav.tsx` | 日报 Tab / 移动端「更多」红点 |

---

## 3. 验收

- fast 模式流式生成时，日报页可见右侧阶段侧栏与输出摘要
- 点击「停止生成」后流中断，不 fallback async
- 生成中切到持仓 Tab，底部浮层显示进度；完成后红点 + 桌面通知（已授权时）
- vitest：`streamingStageMeta` / `ReportPanel` 流式用例通过

**Plan：** `docs/superpowers/plans/2026-06-25-report-pipeline-speedup-phase3.md`
