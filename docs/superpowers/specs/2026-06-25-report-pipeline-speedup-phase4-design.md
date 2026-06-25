# 日报 / 推荐报告管线提速 · 阶段 4 设计：流式增强包

**版本：** 2026-06-26  
**前置：** 阶段 1–3 已落地（数据优化 + SSE 流式 + UI 精修）  
**用户确认（2026-06-26）：** 交付顺序 **A**；中途追加 prompt **v1 仅 pre-LLM**

---

## 1. 背景与竞品调研

### 1.1 竞品行为（2026/02 参考）

| 产品 | 中途追加 | 流式粒度 | 等待行为 |
|---|---|---|---|
| **ChatGPT Deep Research** | generating 过程中可 follow-up，**不重启**，更新 research plan 继续 | 阶段卡片 + 最终报告 token | 可离开 + 通知 + 全屏阅读 |
| **Gemini Deep Research** | 运行前可 Edit plan；完成后可追问补章节 | 阶段为主 | 可切 tab |
| **Notion AI** | Stop 打断 | **token 级 inline** 光标 | 可编辑其他区域 |

来源：OpenAI Deep Research 2026/02 更新（mid-run steering）、Gemini Edit plan、Notion inline streaming。

### 1.2 FundPilot 现状

| 能力 | 状态 |
|---|---|
| fast 日报 SSE + partial + 骨架 | ✅ 阶段 2–3 |
| `token` 事件后端已 emit | ✅ `analyze_streaming` |
| 前端消费 `token` | ❌ 未接 |
| deep 模式流式 | ❌ 仍 async 轮询 |
| 荐基流式 | ❌ 仍 async |
| 中途追加 | ❌ |

### 1.3 架构约束（本仓库）

- **deep 模式** = `news_tool_max_rounds > 0` 的多轮 tool calling + 最终 JSON（`deepseek_client._generate_with_tools`）
- **fast 模式** = 单次 JSON 流式（`analyze_streaming`）
- **荐基** = `discovery_pipeline` 多 stage + `DiscoveryClient._call_model` 单次非流式 JSON
- 增量解析器 `StreamingReportParser` 已支持 `fund_recommendations[]`，可泛化为字段名参数

---

## 2. 目标（North Star）

| 子阶段 | 目标 | 用户感知 |
|---|---|---|
| **4.1 打字机** | 消费已有 `token` 事件 | generating 阶段可见「正在输出…▍」而非静态 spinner |
| **4.2 荐基流式** | `POST /api/fund-discovery/stream` | 荐基与日报同等 TTFB / 骨架体验 |
| **4.3 deep 流式** | deep 日报走 SSE；**tool 轮仍同步**，仅最终 JSON 流式 | deep 用户不再 90s+ 黑盒 |
| **4.4 中途追加** | pre-LLM stage 内 `POST .../followup` 注入用户说明 | 数据装配阶段可纠偏，无需重跑 |

**不做（阶段 4 范围外）：**

- generating 中途 salvage + 续写（留 4.5）
- deep tool-calling 轮次流式 delta（留 4.6）
- 荐基中途追加（复用 4.4 模式后再开）

---

## 3. 子阶段 4.1：Token 级打字机光标

### 3.1 方案（推荐）

**纯前端**，复用后端已有 `{"type":"token","content":"..."}`。

- `streamApi` 增加 `onToken?: (chunk: string) => void`
- `StreamingReportState.tokenBuffer: string`（累积，上限 2KB 环形截断）
- `ReportThinkingSidebar` 在 `stage === "generating"` 时展示：
  - 折叠区「模型原始输出（预览）」：等宽字体 + 尾部 `▍` 闪烁
  - 不替代 `report_partial` 卡片（partial 仍是结构化 UX）

### 3.2 文件

| 路径 | 改动 |
|---|---|
| `apps/web/src/lib/streamApi.ts` | dispatch `token` |
| `apps/web/src/components/Dashboard.tsx` | 累积 tokenBuffer |
| `apps/web/src/components/ReportThinkingSidebar.tsx` | 打字机 UI |
| `apps/web/src/components/ReportPanel.test.tsx` | token 预览断言 |

### 3.3 验收

- mock SSE 推 `token` 事件 → 侧栏可见预览 + 光标
- `tsc` + vitest 通过

---

## 4. 子阶段 4.2：荐基流式

### 4.1 方案

镜像 `analyze_streaming`：

```
POST /api/fund-discovery/stream
  body: DiscoveryRequest
  events: stage | skeleton | token | report_partial | done | error
```

- 新增 `discovery_streaming.py`：复用 `run_discovery` 前半段发 stage；skeleton 用 `candidate_pool` fund_codes
- `StreamingReportParser` 泛化：`array_field="recommendations"`，`partial_field="recommendation"`
- 前端：`FundDiscoveryPanel` fast 模式默认流式 + `DiscoveryStreamingFloat`（镜像日报）

### 4.2 文件（估算）

| 新增 | 责任 |
|---|---|
| `discovery_streaming.py` | SSE 生成器 |
| `test_discovery_streaming.py` | mock LLM |
| `test_discovery_stream_endpoint.py` | 端点 |
| `apps/web/src/lib/discoveryStreamApi.ts` | 客户端 |
| `DiscoverySkeleton.tsx` | 荐基骨架 |

### 4.3 验收

- mock 测试 event 顺序含 `done`
- `FundDiscoveryPanel` 流式时可见推荐卡 skeleton

---

## 5. 子阶段 4.3：Deep 模式流式（仅最终 JSON）

### 5.1 方案

**分两段，对用户一条 SSE：**

1. **同步 tool 轮**（与现 `_generate_with_tools` 相同）：每轮 emit `stage`（`tool_round_N` / `fetch_market_news`）
2. **流式最终 JSON**：`stream_chat_completion` + `StreamingReportParser`
3. 后续 judge/save 同 fast

`analyze_streaming` 移除 `analysis_mode != fast` 拒绝；deep 走新函数 `_stream_deep_report_llm(client, ...)`.

**不做：** tool_calls 的 streaming delta（OpenAI `delta.tool_calls` 拼接复杂，收益低于最终 JSON 流式）。

### 5.2 风险

| 风险 | 缓解 |
|---|---|
| deep 总耗时不变 | 阶段事件 + partial 改善感知 |
| tool 轮仍阻塞 10–30s | 侧栏显示「正在检索新闻 (2/3)」 |

### 5.3 验收

- deep 模式 `smoke_run_analysis --stream --mode deep` TTFB < 5s（首个 stage）
- 127+ 单测仍全过

---

## 6. 子阶段 4.4：中途追加 Prompt（pre-LLM）

### 6.1 方案

**会话模型：** 每个 SSE 连接一个 `stream_session_id`（uuid），内存 dict 存可变上下文（**不持久化**，断线丢弃）。

```
POST /api/analyze/stream          → 返回 SSE，首事件含 session_id
POST /api/analyze/stream/{id}/followup  → body: { "message": "..." }
```

- followup 仅在 `stage` ∈ `{fund_data, news_prefetch, news_summarize}` 时生效
- 进入 `generating` 后返回 409
- 消息追加到 `AnalysisFactsBundle` 或 `request` 侧 `stream_notes: list[str]`，写入 user payload 的 `operator_notes` 字段

**前端：** 侧栏底部输入框「补充说明」+ 发送；仅 pre-LLM 可编辑。

### 6.2 竞品对齐度

ChatGPT 2026/02 可在 generating 中追加；我们 v1 仅 pre-LLM（用户已确认），generating 重定向留 4.5。

### 6.3 验收

- 在 news_prefetch 阶段 followup → 后续 LLM payload 含该 note
- generating 阶段 followup → 409

---

## 7. 实施顺序与 PR 切分

| PR | 子阶段 | 依赖 |
|---|---|---|
| PR-1 | 4.1 打字机 | 无 |
| PR-2 | 4.2 荐基流式 | 4.1 可选 |
| PR-3 | 4.3 deep 流式 | 4.1 |
| PR-4 | 4.4 pre-LLM followup | 4.1–4.3 任一 SSE 稳定 |

---

## 8. 后续（阶段 5 候选）

- 4.5 generating 中途 salvage + 续写
- 4.6 deep tool-calling streaming
- 荐基 pre-LLM followup 复用

**Plan：** 子阶段落地后分别更新 `docs/superpowers/plans/2026-06-25-report-pipeline-speedup-phase4-{n}.md`
