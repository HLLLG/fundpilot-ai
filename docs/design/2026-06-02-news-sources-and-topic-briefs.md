# 新闻多源 + 按主题批量摘要 — 改动清单

**日期：** 2026-06-02  
**状态：** 已实现（2026-06-02）  
**目标：** 在不大改主流程的前提下，提升日报新闻参考价值；控制延迟与 API 成本（Flash 按主题摘要，非逐篇）。

**原则：**

- 主报告仍用 Pro/深度 或现有 fast/deep 分工；**摘要仅用 Flash**。
- 保留 `prefetched_news`（原始条目）供 `news_citation` 校验；新增 `topic_briefs` 供模型读。
- 任一环节失败 → **降级** 为当前行为（标题 + 200 字 snippet）。
- 不传全文爬虫、不上传截图。

---

## 阶段 0：准备（0.5 天）

| # | 任务 | 说明 |
|---|------|------|
| 0.1 | 确认 AkShare 可用接口 | 在本地 REPL 试 `stock_news_em`、宏观类接口（见阶段 1.2 候选）；记录列名与限频表现。 |
| 0.2 | 定稿 `TopicBrief` JSON schema | 见下文「数据模型」。 |
| 0.3 | 更新 `.env.example` 占位 | 阶段 2 的环境变量先写入示例，避免实现时遗漏。 |

---

## 阶段 1：数据模型与配置（P0）

### 1.1 模型 `apps/api/app/models.py`

新增：

```python
class TopicBriefPoint(BaseModel):
    headline: str          # 一句事实，≤80 字
    sentiment: Literal["bullish", "bearish", "neutral"]
    is_today: bool
    source_titles: list[str]  # 对应 prefetched NewsItem.title，供 citation
    source_urls: list[str] = []

class TopicBrief(BaseModel):
    topic: str
    summary: str           # 2～4 句总括，≤300 字
    points: list[TopicBriefPoint] = Field(max_length=5)
    news_count: int = 0
    summarized_at: datetime | None = None
    provider: str = "deepseek-flash"  # 或 "rule-fallback"
```

`Report` 增加可选字段（向后兼容）：

- `topic_briefs: list[TopicBrief] = []`
- 保留 `market_news: list[NewsItem]` 不变

### 1.2 配置 `apps/api/app/config.py` + `.env.example`

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_NEWS_SOURCES` | `eastmoney,announcement` | 逗号分隔：`eastmoney`,`announcement`,`macro` |
| `FUND_AI_NEWS_SUMMARIZE` | `true` | 是否做按主题 Flash 摘要 |
| `FUND_AI_NEWS_SUMMARIZE_MODEL` | 同 `deepseek_model_fast` | 摘要模型 |
| `FUND_AI_NEWS_SUMMARIZE_MAX_POINTS` | `5` | 每主题最多要点条数 |
| `FUND_AI_NEWS_SUMMARIZE_TIMEOUT` | `60` | 单主题摘要超时秒 |
| `FUND_AI_NEWS_MACRO_TOPIC` | `A股` 或 `上证指数` | 宏观源检索词（实现时按 AkShare 定） |

README / `docs/PROJECT_CONTEXT.md` 同步一节「新闻管道」。

---

## 阶段 2：新闻采集层重构（P0）

### 2.1 拆分 `apps/api/app/services/news_service.py`

建议结构（可单文件内用类/函数分区，不必过度拆包）：

| 模块/函数 | 职责 |
|-----------|------|
| `NewsProvider` 协议 | `search(topic, limit) -> list[NewsItem]` |
| `EastMoneyStockNewsProvider` | 现有 `_from_eastmoney` |
| `FundAnnouncementProvider` | 现有 `_from_fund_announcements` |
| `MacroNewsProvider`（新） | 阶段 1 选的 AkShare 宏观接口；失败则返回 `[]` |
| `NewsService.search()` | 按 `settings.news_sources` 合并、去重、排序 |
| `NewsService.prefetch_for_holdings()` | 不变入口；内部 `topics_from_holdings` + 可选 **自动加宏观主题**（持仓≥1 且启用 `macro`） |

**去重键：** 保持 `url` 或 `topic:title`；跨源同标题合并时保留 `source` 最早/最完整 snippet。

### 2.2 宏观源（二选一，实现时定）

| 方案 | AkShare 方向 | 备注 |
|------|----------------|------|
| A | `stock_news_em(symbol="上证指数")` 或板块指数名 | 实现快，与东财同接口 |
| B | 财经要闻类接口（需查当前 AkShare 文档） | 更贴宏观，但接口可能变 |

**验收：** `tests/test_news_service.py` 增加 mock 多源合并、macro 开关关闭时不拉宏观。

### 2.3 不涉及

- 全文网页抓取（Firecrawl 等）— 本期不做。
- 雪球/社交媒体 — 本期不做。

---

## 阶段 3：按主题 Flash 摘要（P0 核心）

### 3.1 新建 `apps/api/app/services/news_summarizer.py`

| 函数 | 说明 |
|------|------|
| `group_news_by_topic(items) -> dict[str, list[NewsItem]]` | 按 `NewsItem.topic` 分组 |
| `summarize_topic(topic, items, *, settings) -> TopicBrief` | 单主题一次 Flash 调用 |
| `summarize_all_topics(items, settings) -> list[TopicBrief]` | 并发度 **2**（`asyncio` 或 `ThreadPoolExecutor`），避免打爆 API |
| `build_topic_briefs_offline(topic, items) -> TopicBrief` | 无 Key / 超时：仅取 Top3 标题拼 `summary`，`provider=rule-fallback` |

**Flash 输入（user message JSON）：**

```json
{
  "topic": "半导体",
  "today": "2026-06-02",
  "items": [
    {"title": "...", "published_at": "...", "snippet": "...", "is_today": true}
  ],
  "rules": [
    "只根据 items 压缩，不得编造数字、公司名、涨跌幅",
    "合并重复事件",
    "输出严格 JSON，匹配 TopicBrief schema"
  ]
}
```

**Flash 输出：** 解析为 `TopicBrief`；失败 → `build_topic_briefs_offline`。

**调用方式：** 复用 `deepseek_http` + `settings.deepseek_model_fast`；`max_tokens` 建议 1024～2048/主题。

### 3.2 接入 `apps/api/app/services/deepseek_client.py`

在 `generate_report()` 内，`prefetch_for_holdings` 之后：

```text
market_news = news_service.prefetch_for_holdings(...)
topic_briefs = []
if settings.news_summarize and settings.deepseek_configured:
    topic_briefs = summarize_all_topics(market_news, settings)
```

修改：

| 位置 | 改动 |
|------|------|
| `_user_payload()` | 增加 `"topic_briefs": [...]`；`requirements` 增加「优先依据 topic_briefs，引用须对应 source_titles」 |
| `_system_prompt()` | 说明 `topic_briefs` 为预摘要，`prefetched_news` 为原始出处 |
| `_offline_report()` / `Report(...)` | 写入 `topic_briefs`（离线可为 rule-fallback 或 `[]`） |
| Tool `fetch_market_news` | 返回仍可为 `NewsItem[]`；可选：工具执行后再对**新主题**补一次 `summarize_topic`（P1） |

### 3.3 守卫 `apps/api/app/services/news_citation.py`

| 改动 | 说明 |
|------|------|
| `_news_titles()` | 合并 `market_news` 标题 + 所有 `topic_briefs[].points[].source_titles` |
| 无匹配时 | 保持替换为「暂无明确利好/利空」 |

### 3.4 推荐 enrichment `recommendations.py`

`classify_sector_news` / `attach_sector_news`：

- 优先用 `TopicBriefPoint.sentiment` + `headline` 填充 `news_bullish` / `news_bearish`（仍须通过 citation 守卫）。
- 无 brief 时回退现有「标题关键词」逻辑。

---

## 阶段 4：分析与运行时（P1）

### 4.1 `apps/api/app/services/analysis_facts.py`

可选增加只读块（不强制）：

```python
"news": {
  "topic_count": len(topic_briefs),
  "today_point_count": ...,
}
```

### 4.2 `apps/api/app/services/analysis_runtime.py`

| 模式 | 建议 |
|------|------|
| fast | `news_summarize=true`，主题数仍 `min(3, news_max_topics)` |
| deep | 同左；Tool 补拉的新闻在回合末可对**新 topic** 再摘要（P1） |

### 4.3 报告追问 `report_chat.py`

| 项 | 改动 |
|----|------|
| `report_to_markdown()` | 增加「主题要闻摘要」小节（来自 `topic_briefs`） |
| 深度追问 Tool | 行为与现有一致；Markdown 已含 brief 时可少调 Tool |

---

## 阶段 5：前端（P2，可选）

| 文件 | 改动 |
|------|------|
| `apps/web/src/lib/api.ts` | `Report` 类型增加 `topic_briefs` |
| `ReportPanel.tsx` 或新 `ReportNewsBriefPanel.tsx` | 按主题折叠展示 summary + points；链接到原文 title（无 url 则纯文本） |
| 分析模式说明 | 快速/深度 tooltip 提一句「新闻已按主题预摘要」 |

**本期可不做 UI**：仅 API + Markdown 导出可见即可。

---

## 阶段 6：测试（P0）

| 文件 | 用例 |
|------|------|
| `tests/test_news_service.py` | 多源开关；macro 关闭；去重 |
| `tests/test_news_summarizer.py`（新） | mock Flash 返回 JSON；解析失败 → offline；禁止编造（snapshot 固定输入） |
| `tests/test_news_citation.py` | brief 中 `source_titles` 可通过 citation |
| `tests/test_golden_pipeline.py` | 报告含 `topic_briefs` 字段；离线模式不报错 |
| `tests/test_deepseek_tools.py` | `_user_payload` 含 `topic_briefs`（mock） |

**Fixture：** `tests/fixtures/news_summarizer_flash_response.json`

---

## 阶段 7：可观测与降级（P1）

| 项 | 说明 |
|----|------|
| 日志 | 每主题：条数、摘要耗时、provider（flash/offline） |
| 报告 `provider` 旁注 | 可选 `news_summarize: flash|skipped|failed` 写入 `Report` metadata 或 caveats |
| 关闭摘要 | `FUND_AI_NEWS_SUMMARIZE=false` → 与现网一致 |
| 关闭所有新闻 | `FUND_AI_NEWS_ENABLED=false` |

---

## 阶段 8：文档（P0）

| 文件 | 内容 |
|------|------|
| `docs/PROJECT_CONTEXT.md` | 新闻管道图、环境变量、喂给模型的字段说明 |
| `README.md` | 隐私段：摘要由 Flash 处理，仍不传截图 |
| 本文档 | 实施后改 **状态：已实现** |

---

## 建议实施顺序（给 AI / 人工）

```text
1. 模型 + config（阶段 1）
2. news_service 多源（阶段 2）
3. news_summarizer + deepseek_client 接入（阶段 3）
4. news_citation + tests（阶段 3.3 + 6）
5. report_export / report_chat Markdown（阶段 4.3）
6. 前端展示（阶段 5，可选）
```

**预估工作量：** 后端核心 1～2 天；含测试与文档；加 UI 再 +0.5 天。

---

## 验收标准（Definition of Done）

1. 生成日报时，`Report` 同时含 `market_news` 与 `topic_briefs`（至少 1 个持仓主题）。
2. 关闭 `FUND_AI_NEWS_SUMMARIZE` 后行为与当前线上一致。
3. Flash 失败时报告仍能生成，`topic_briefs` 为 rule-fallback 或空，caveats 不误导。
4. `news_bullish` / `news_bearish` 无引用标题时仍被 `news_citation` 替换为「暂无明确…」。
5. `pytest tests` 全绿；手动跑一天真实持仓，深度模式报告新闻段可读性明显优于仅标题+snippet。

---

## 后续（不在本期）

- 向量检索 + 历史新闻库
- 每篇独立摘要（成本高，仅在单主题新闻 >15 条时考虑）
- 报告追问实时再摘要 Tool 结果
- 新闻缓存表（按 topic+date TTL，减少重复 Flash）
