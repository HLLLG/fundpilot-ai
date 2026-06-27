# 日报 / 荐基管线提速 B1 设计

**日期：** 2026-06-26
**方案：** B1，缓存与并行 + stage 耗时埋点
**状态：** 用户确认方案 B 后执行第一批

## 背景

上一轮修复解决了新闻/摘要超时不生效和荐基切 Tab 丢任务的问题，但日报与荐基仍存在可优化的串行等待：

- 日报先等 `FundDataService.get_snapshots_with_nav_trends`，再等 `NewsService.prefetch_for_holdings`。
- 荐基先构建/增强候选池，再拉新闻。
- 前端和 smoke 只能靠客户端时间推断阶段耗时，SSE stage 本身没有服务端累计耗时。

## 目标

- 日报流式路径中，基金净值/诊断数据与新闻预取并行。
- 荐基流式路径中，目标板块确定后立即预取新闻，同时构建/增强候选池。
- 每个 SSE stage 事件包含 `elapsed_ms`，用于 smoke、日志和后续 UI 诊断。
- 不改变报告内容、LLM prompt 结构和投资决策逻辑。

## 设计

### 用户上下文

项目通过 `request_context.ContextVar` 维护当前用户。普通线程不会自动继承 ContextVar，所以新增小工具：

- `pipeline_concurrency.run_with_request_user(user_id, fn)`
- 在线程中先 `set_request_user_id(user_id)`，执行 `fn` 后 reset。

这样即使未来 worker 内部访问需要用户上下文的缓存/DB，也不会出现“未设置当前用户上下文”。

### 日报流式并行

在 `stream_analysis` 中：

1. 完成持仓解析与风险评估。
2. 解析 runtime。
3. 用 `ThreadPoolExecutor(max_workers=2)` 同时执行：
   - `FundDataService().get_snapshots_with_nav_trends(enriched.holdings)`
   - `NewsService().prefetch_for_holdings(enriched.holdings, max_topics=runtime.news_max_topics)`
4. 两个结果都返回后进入 `news_summarize`。

### 荐基流式并行

在 `stream_discovery` 中：

1. 完成板块热度与 target sectors 选择。
2. 基于 target sectors + focus sectors 生成 news topics。
3. 后台启动 `NewsService().prefetch_topics(topics)`。
4. 主线程继续执行候选池构建与 `enrich_candidates`。
5. 候选池 ready 后等待 news future，进入摘要和 facts 构建。

### Stage 耗时

`stream_analysis` 与 `stream_discovery` 在函数开始记录 `started_at = time.monotonic()`。每次 emit stage 时附加：

```json
{"type": "stage", "stage": "news_prefetch", "label": "...", "elapsed_ms": 1234}
```

前端现有解析会忽略额外字段，因此兼容现有 UI。

## 验收

- 模拟日报 fund data 和 news 各阻塞 0.35s，整体小于 0.55s。
- 模拟荐基候选池 0.35s、enrich 0.35s、news 0.35s，整体小于 0.9s。
- deep 流式 stage 事件均带 `elapsed_ms`。
- 现有 streaming / news / frontend focused tests 通过。

