# Phase 4 实施计划：流式增强四项

**日期：** 2026-06-25  
**规格：** [phase4-design](../specs/2026-06-25-report-pipeline-speedup-phase4-design.md)

## 交付顺序（用户确认）

1. 4.1 Token 级打字机光标  
2. 4.2 荐基流式  
3. 4.3 Deep 模式流式（仅最终 JSON）  
4. 4.4 Pre-LLM 中途追加 prompt  

## 4.1 Token 打字机 ✅

- `streamApi.ts`：`onToken`、`tokenBuffer`、`appendStreamTokenBuffer`
- `Dashboard.tsx`：累积 token
- `ReportThinkingSidebar.tsx`：`generating` 阶段 monospace 预览 + `▍` 光标

## 4.2 荐基流式 ✅

**后端**

- `streaming_json_parser.py`：泛化 `array_field` / `item_partial_field`
- `discovery_streaming.py`：SSE 生成器
- `POST /api/fund-discovery/stream`
- `discovery_client.py`：`build_discovery_report_from_parsed`
- 测试：`test_discovery_streaming.py`、`test_discovery_stream_endpoint.py`

**前端**

- `discoveryStreamApi.ts`
- `DiscoverySkeleton.tsx`
- `FundDiscoveryPanel.tsx`：fast 流式 + async 回退

## 4.3 Deep 模式流式 ✅

- `deepseek_client.py`：`run_news_tool_rounds`（同步 tool 轮 + `on_stage`）
- `analyze_streaming.py`：deep 走 tool 轮后发 stage，最终 JSON `stream_chat_completion`
- `Dashboard.tsx`：fast / deep 均默认流式
- 测试：`test_stream_analysis_deep_emits_tool_stages_and_done`

## 4.4 Pre-LLM Followup ✅

- `stream_session_store.py`：内存会话 + `operator_notes`
- `POST /api/analyze/stream/{session_id}/followup`（pre-LLM 200，generating 409）
- `analysis_payload.build_user_payload`：`operator_notes` 字段
- 首条 SSE：`{ type: "session", session_id }`
- 前端：侧栏「补充说明」输入框 + `submitStreamFollowup`
- 测试：`test_stream_session_store.py`、`test_stream_followup_endpoint.py`

## 验证

```bash
cd apps/api && python -m pytest -q          # 135 passed
cd apps/web && npx tsc --noEmit
cd apps/web && npm test -- --run src/components/ReportPanel.test.tsx src/lib/streamApi.test.ts
```

## 未做（留 4.5）

- generating 阶段中途追加（ChatGPT Deep Research 全量行为）
- tool_calls streaming delta
- 荐基 pre-LLM followup（可复用 4.4 模式）

## 补充（2026-06-26）

- ✅ `DiscoveryStreamingFloat` + 发现 Tab 红点 + 桌面通知（流式完成且用户已离开）
- ✅ deep 荐基流式：`DiscoveryClient.run_discovery_news_tool_rounds` + `discovery_streaming` 支持 deep
