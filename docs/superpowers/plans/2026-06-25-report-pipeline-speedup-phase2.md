# 日报/推荐报告管线提速 · 阶段 2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]` / `- [ ]`) syntax for tracking.

**Goal:** 把用户感知耗时从「等 70~95s 黑盒」降到「<3s 首字节 + 渐进显示」——后端总耗时不变，fast 模式走 SSE 流式 + 前端骨架卡；deep 模式与 async 轮询兜底保留。

**Architecture:** 后端新增 `stream_analysis` 生成器（stage → skeleton → token/partial → judge/save → done），增量 JSON 解析器从 LLM chunk 提取 `fund_recommendations` 元素；前端 `streamAnalysis` 消费 SSE，Dashboard fast 模式默认走流式、失败回退 async。

**Tech Stack:** Python 3.11+ / FastAPI / httpx streaming / pytest · Next.js / React / vitest / fetch ReadableStream SSE 解析

**Spec:** `docs/superpowers/specs/2026-06-25-report-pipeline-speedup-phase2-design.md`

**Status:** 2026-06-25 落地完成（步骤 1–9 + 前端单测；步骤 10 Playwright 可选未做）

---

## 任务总览

| 任务 | 范围 | 状态 |
|---|---|---|
| 1 | `streaming_json_parser` 单测 + 实现 | ✅ |
| 2 | `deepseek_streaming.stream_chat_completion` | ✅ |
| 3 | `deepseek_client._build_final_report` 抽取 | ✅ |
| 4 | `analyze_streaming` + 单测 | ✅ |
| 5 | `POST /api/analyze/stream` | ✅ |
| 6 | smoke `--stream` | ✅ |
| 7 | `streamApi.ts` | ✅ |
| 8 | `ReportSkeleton` + `ReportPanel` | ✅ |
| 9 | `Dashboard` 流式默认 + 回退 | ✅ |
| 10 | Playwright 端到端（可选） | ⬜ 未做 |

---

## Task 1: `streaming_json_parser` — 增量 JSON 解析

**Files:**
- Create: `apps/api/app/services/streaming_json_parser.py`
- Create: `apps/api/tests/test_streaming_json_parser.py`

- [x] **Step 1: 写失败测试**（chunk 切 brace / 转义符 /  premature emit）
- [x] **Step 2: 实现 `StreamingReportParser.feed` + `_FundRecommendationScanner`**
- [x] **Step 3: 跑测试**

Run: `cd apps/api && python -m pytest tests/test_streaming_json_parser.py -v`

---

## Task 2: `deepseek_streaming` — 流式 LLM 调用

**Files:**
- Create: `apps/api/app/services/deepseek_streaming.py`

- [x] **Step 1: 实现 `stream_chat_completion`**（复用 `report_chat._parse_stream_line` + `_build_chat_payload`）
- [x] **Step 2: chunk 间 read timeout 30s**

---

## Task 3: `_build_final_report` 重构

**Files:**
- Modify: `apps/api/app/services/deepseek_client.py`

- [x] **Step 1: 抽出 `_build_final_report()` 与 `build_analysis_chat_messages()`**
- [x] **Step 2: `generate_report` 改调 `_build_final_report`**
- [x] **Step 3: 全量回归** — `python -m pytest tests/ -q`

---

## Task 4: `analyze_streaming` 端到端

**Files:**
- Create: `apps/api/app/services/analyze_streaming.py`
- Create: `apps/api/tests/test_analyze_streaming.py`

- [x] **Step 1: 实现 `stream_analysis`**（仅 fast；deep 返回 error）
- [x] **Step 2: mock LLM 单测**（skeleton + partial + done；断流 salvage）
- [x] **Step 3: 跑测试**

Run: `cd apps/api && python -m pytest tests/test_analyze_streaming.py -v`

---

## Task 5: SSE 端点

**Files:**
- Modify: `apps/api/app/main.py`

- [x] **Step 1: `POST /api/analyze/stream`**（`StreamingResponse` + `X-Accel-Buffering: no`）

---

## Task 6: smoke 脚本

**Files:**
- Modify: `apps/api/scripts/smoke_run_analysis.py`

- [x] **Step 1: 加 `--stream` 选项**
- [x] **Step 2: 输出 TTFB / 首 partial / 总耗时**

Run（需真实 API Key）:
```bash
cd apps/api && python scripts/smoke_run_analysis.py --mode fast --label baseline
cd apps/api && python scripts/smoke_run_analysis.py --mode fast --label stream --stream
```

---

## Task 7: 前端 SSE 客户端

**Files:**
- Create: `apps/web/src/lib/streamApi.ts`
- Modify: `apps/web/src/lib/api.ts`（re-export）
- Create: `apps/web/src/lib/streamApi.test.ts`

- [x] **Step 1: `streamAnalysis()` fetch + ReadableStream SSE 解析**
- [x] **Step 2: 5s 首事件超时 → 抛错供 Dashboard 回退**
- [x] **Step 3: vitest 单测**

Run: `cd apps/web && npm test -- src/lib/streamApi.test.ts`

---

## Task 8: 骨架卡 UI

**Files:**
- Create: `apps/web/src/components/ReportSkeleton.tsx`
- Modify: `apps/web/src/components/ReportPanel.tsx`
- Create: `apps/web/src/components/ReportPanel.test.tsx`

- [x] **Step 1: `ReportSkeleton`**（stage label + N 张骨架卡 + partial 填充）
- [x] **Step 2: `ReportPanel` 在 `streaming && !report` 时渲染骨架**
- [x] **Step 3: vitest**（stage / skeleton 数量 / partial / done 视图）

Run: `cd apps/web && npm test -- src/components/ReportPanel.test.tsx`

---

## Task 9: Dashboard 切换

**Files:**
- Modify: `apps/web/src/components/Dashboard.tsx`

- [x] **Step 1: fast 模式默认 `streamAnalysis`**
- [x] **Step 2: 失败回退 `startAnalyzeJob` + `JobStatusFloat`**
- [x] **Step 3: deep 模式仍走 async**

---

## Task 10: Playwright 端到端（可选）

- [ ] **未实施** — 可在 CI 加一条 mock SSE 的 e2e，或手动联调验证。

---

## 验证清单

```bash
# 后端全量
cd apps/api && python -m pytest tests/ -q

# 前端阶段 2 相关
cd apps/web && npm test -- src/lib/streamApi.test.ts src/components/ReportPanel.test.tsx
cd apps/web && npm run typecheck
```

**落地时实测：** API 126 passed；`ReportPanel.test.tsx` + `streamApi.test.ts` 7 passed。

---

## 已知限制（spec §10）

- streaming **仅 fast 模式**；deep 继续 async 轮询
- 客户端断线 → 服务端 generator 关闭，已生成部分丢弃
- 增量 partial 丢失时 `done` 事件仍含完整 Report（`_parse_model_json` 兜底）
