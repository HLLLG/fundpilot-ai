# 日报 / 荐基流式稳定性修复设计

**日期：** 2026-06-26
**方案：** B，中等修复
**状态：** 用户已确认

## 背景

6 月 25 日日报与荐基完成流式重构后，两个功能出现可用性回退：

- 荐基拉取新闻摘要慢，切到日报再返回荐基时任务进度消失。
- 日报生成时卡在要闻摘要或生成前阶段很久。

竞品侧，OpenAI Deep Research、Gemini Deep Research、Perplexity 都把长任务做成可离开页面、可持续显示进度、可逐步暴露来源/片段的体验。好基灵的流式任务也应满足“离开 Tab 不丢、慢外部数据可降级、进度可信”。

## 根因

1. 荐基流任务由 `FundDiscoveryPanel` 子组件启动和持有。Dashboard 按 Tab 条件挂载该组件；切到日报 Tab 时组件卸载，`useEffect` cleanup 会调用 `discoveryStreamAbortRef.current?.abort()`，导致流连接被主动取消。
2. `NewsService.prefetch_topics` 虽然有总 deadline，但 `ThreadPoolExecutor` context manager 退出会等待未完成 future，导致慢 topic 仍阻塞返回。
3. `news_summarizer.summarize_all_topics` 的 per-future timeout 不能限制整体等待；`as_completed` 与线程池退出同样会等待慢摘要。
4. 默认新闻链路预算偏宽：单主题新闻抓取 20s、总预取 45s、摘要 60s，多个主题叠加后会造成用户感知“卡住”。

## 目标

- 荐基流任务离开荐基 Tab 后继续运行，返回荐基 Tab 能看到进度；只有用户点击取消才中止。
- 新闻预取和主题摘要遵守总预算，超时后用已有结果或规则摘要降级。
- 用回归测试锁住慢请求、慢摘要、切 Tab 不丢任务三类行为。
- 保留现有 SSE 协议和后台 async 回退路径，不重做报告架构。

## 非目标

- 不做持久化流式会话恢复，刷新浏览器后仍按现有行为重新开始或查历史。
- 不做 LLM tool-calls delta 流式解析。
- 不改变推荐/日报的投资决策规则。

## 设计

### 后端新闻预算

`NewsService.prefetch_topics` 改为手动管理 `ThreadPoolExecutor`：

- 启动每个 topic future。
- `as_completed(..., timeout=remaining)` 收集预算内完成的结果。
- 超时或异常时跳过该 topic。
- `executor.shutdown(wait=False, cancel_futures=True)`，不等待已超时任务。

`summarize_all_topics` 采用同样模式：

- 使用同一个 `news_summarize_timeout_seconds` 作为整体预算。
- 预算内完成的主题使用 Flash 摘要。
- 未完成或失败的主题立即补 `build_topic_briefs_offline`。
- 退出时不等待慢 future。

### 前端荐基任务生命周期

保留现有荐基流式运行路径，但取消“Tab 卸载即中止”的副作用：

- `Dashboard` 继续持有 `streamingDiscovery` 和 `discoveryStreamAbortRef`。
- `FundDiscoveryPanel` 仍可启动扫描，并通过父级 state 更新跨 Tab 浮层。
- 移除 `FundDiscoveryPanel` 卸载时自动 abort 的 cleanup；组件卸载不再等同于用户取消。
- `DiscoveryStreamingFloat` 继续显示跨 Tab 进度，取消按钮仍调用 Dashboard 的显式 cancel。

### 验证

后端：

- 新增/加强 `test_prefetch_topics_total_timeout_does_not_wait_for_blocked_workers`。
- 新增 `test_summarize_all_topics_total_timeout_falls_back_without_waiting_for_blocked_workers`。

前端：

- 新增 `FundDiscoveryPanel` 生命周期测试：活动流存在时组件卸载不会调用 abort。

烟测：

- `pytest tests/test_news_service_prefetch.py tests/test_news_summarizer.py tests/test_discovery_streaming.py`
- `npm test -- --run src/components/Dashboard.discovery-stream.test.tsx src/lib/discoveryStreamApi.test.ts`
- `npx tsc --noEmit`
