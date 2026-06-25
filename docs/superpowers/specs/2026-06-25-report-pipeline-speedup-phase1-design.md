# 日报 / 推荐报告 管线提速 · 阶段 1 设计

**版本：** 2026-06-25
**作者：** hegl + Claude (Opus 4.7)
**阶段定位：** 三阶段计划的第一阶段——仅后端数据装配 / 缓存修复，不动 LLM 调用与前端 UI。

---

## 1. 背景

`/api/analyze/async`（生成日报）与 `/api/fund-discovery/async`（推荐基金）当前冷启动下 5min+，热启动也常 1~2min。已有一轮缓存优化（详见 `PROJECT_CONTEXT.md` 2026-06-25「DeepSeek 日报数据管线缓存优化」「板块资金流日期对齐修复」），但在静态审计后发现：

1. **`build_analysis_facts` 在一次报告生成里被算了 3 次**（`prepare_analysis_bundle` + `report_judge._rule_judge` + `report_judge._llm_judge`），且后两次入参不完整（无 nav_trends/factor_scores/risk_metrics），与第一次结果不一致。
2. **`discovery_candidate_pool.build_candidate_pool` 与 `dip_drop_scanner.build_dip_pool_for_sectors` 直接调 `akshare_subprocess.fetch_open_fund_rank`**，绕过了 `fund_rank_cache` 的 1h 全用户共享缓存——而因子分模块走的是带缓存版本，两套数据不共享。
3. **`NewsService.prefetch_topics` 串行 `for topic in topics: search(topic)`**，5 主题 × 1~3 次 AkShare 调用，冷态 3~8s。后续 `news_summarizer.summarize_all_topics` 已并发，前端拉取却是串行。
4. **`fund_nav_cache` 按 `(code, trading_days)` 分 key**，持仓详情弹窗预热用 252、日报/荐基用 66，两份缓存互不共享；详情预热对日报路径毫无价值。

阶段 1 攻这四项，把热路径省 2~4s、冷路径省 10~30s 拿到手。LLM 流式输出与前端骨架卡分别为阶段 2/3，立项依赖阶段 1 完成后的实测数据。

---

## 2. 不做（明确范围）

- ❌ 不动 DeepSeek 主调用方式（流式留给阶段 2）
- ❌ 不删 `_llm_judge`（用户确认在 deep 模式下保留）
- ❌ 不动 `ReportPanel` / `DiscoveryReportPanel` 前端渲染（骨架卡留给阶段 3）
- ❌ 不引入新缓存类型（仅复用与对齐现有 `fund_rank_cache` / `fund_nav_cache`）
- ❌ 不改 `analysis_facts` 输出 schema（兼容现有报告读取/对比/追问）

---

## 3. 改动详情

### F1. `report_judge` 复用 facts，消除 2 次重复 `build_analysis_facts`

#### 现状（apps/api/app/services/report_judge.py）

```python
def judge_parsed_report(parsed, request, risk, snapshots, runtime) -> tuple[dict, dict]:
    judged = _rule_judge(parsed, request, risk, snapshots)   # 内部 build_analysis_facts(...)
    ...
    reviewed = _llm_judge(judged, request, risk, snapshots, runtime)  # 内部又 build_analysis_facts(...)
```

`_rule_judge` 与 `_llm_judge` 各调用一次 `build_analysis_facts(request.holdings, risk, snapshots, request.profile)`——**没有传入** `nav_trends / factor_scores / risk_metrics / portfolio_trend`，比 `prepare_analysis_bundle` 给 prompt 的版本字段更少，存在事实上的「draft 报告参照的 facts」与「judge 校验用 facts」不一致风险。

#### 改动

修改 `judge_parsed_report` 签名，接收上游已计算好的 facts：

```python
def judge_parsed_report(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    runtime: AnalysisRuntime,
    *,
    facts: dict,           # 新增必填参数，由调用方传入 analysis_bundle.facts
) -> tuple[dict, dict]:
    judged = _rule_judge(parsed, request, risk, facts)
    ...
    reviewed = _llm_judge(judged, request, runtime, facts)
```

`_rule_judge(parsed, request, risk, facts)`、`_llm_judge(parsed, request, runtime, facts)` 都改为直接读取传入 facts，删除内部对 `build_analysis_facts` 的调用，移除 `snapshots` 入参（已包含在 facts 里）。

调用方 `deepseek_client.generate_report`：

```python
analysis_bundle = prepare_analysis_bundle(...)
parsed, market_news = self._generate_with_tools(..., analysis_bundle=analysis_bundle)
...
parsed, judge_meta = judge_parsed_report(
    parsed, request, risk, snapshots, runtime,
    facts=analysis_bundle.facts,  # 透传同一份 facts
)
```

#### 收益

- 深度模式：去掉 `_rule_judge` + `_llm_judge` 各一次的 `build_analysis_facts` 调用，**省 5~10s**（因子/风险/资金流装配各 1~2s）。
- 快速模式：省 `_rule_judge` 那一次，**省 1~3s**。
- 副效应：fact 一致性——judge 看到的字段集合等于 prompt 看到的，能校验得更准。

#### 风险与缓解

- **风险：** facts schema 不稳定时 judge 取字段失败。
  **缓解：** facts schema 已经稳定（`PROJECT_CONTEXT` 多处契约消费方），改 judge 只是「换取数来源」，逻辑不动。
- **风险：** legacy `judge_parsed_report` 旧签名被外部 import。
  **缓解：** `grep -r "judge_parsed_report" apps/api` 仅 `deepseek_client.py` 与对应单测；可放心改签名。

#### 测试

`tests/test_report_judge.py`：
1. 新增 `test_judge_uses_provided_facts_no_recompute`：monkeypatch `build_analysis_facts` 抛异常，验证 judge 仍能跑（证明完全不再调用）。
2. 现有 rule/llm judge 行为测试改为构造 fake facts 注入，验证输出与现状一致。

---

### F2. 荐基 / 大跌池 接入 `fund_rank_cache`

#### 现状

- `discovery_candidate_pool.build_candidate_pool` 默认 `fetch_rank=fetch_open_fund_rank`（`akshare_subprocess` 直调，每次 300 行子进程）。
- `dip_drop_scanner.build_dip_pool_for_sectors` 默认 `fetch_rank=fetch_open_fund_rank`，`build_dip_pool_worst_recent` 默认 `fetch_open_fund_rank_worst_recent`（worst_recent 暂不缓存，本期不动）。
- `portfolio_snapshot.build_factor_scores_payload` 已用 `fund_rank_cache.fetch_open_fund_rank_cached`。

冷调荐基 → 子进程 ~3s；热调因子分 → 缓存 <50ms。两条路绕开。

#### 改动

`discovery_candidate_pool.py`：

```python
# 旧
from app.services.akshare_subprocess import fetch_new_fund_offerings, fetch_open_fund_rank
def build_candidate_pool(..., fetch_rank=fetch_open_fund_rank, ...):

# 新
from app.services.akshare_subprocess import fetch_new_fund_offerings
from app.services.fund_rank_cache import fetch_open_fund_rank_cached
def build_candidate_pool(..., fetch_rank=fetch_open_fund_rank_cached, ...):
```

`dip_drop_scanner.py` 同样把 `build_dip_pool_for_sectors` 的默认 fetcher 从 `fetch_open_fund_rank` 换成 `fetch_open_fund_rank_cached`。`build_dip_pool_worst_recent` 走 worst_recent 不动。

#### 收益

- 荐基「均衡潜力 / 含新发」选基策略：热路径 **省 1~3s**（300 行子进程 → 内存读 dict）。
- 大跌雷达 + dip_swing 扫描：热路径同上。

#### 风险与缓解

- **风险：** `fetch_open_fund_rank_cached` 在缓存空时回退到 `fetch_open_fund_rank`，签名与 fetch_rank kwarg 用法兼容（`limit=300`），已验证。
- **风险：** 单测 monkeypatch 旧的 `fetch_open_fund_rank` 时不再触发。
  **缓解：** 把测试中 monkeypatch 目标也对齐到 `fetch_open_fund_rank_cached`，或注入显式 `fetch_rank=fake`。

#### 测试

`tests/test_discovery_candidate_pool.py`、`tests/test_dip_drop_scanner.py`：
- 验证不显式注入 fetcher 时，调用走 `fund_rank_cache.fetch_open_fund_rank_cached`（mock 该函数确认被调到、且子进程函数不再被调）。

---

### F3. `NewsService.prefetch_topics` 主题并发拉取

#### 现状（apps/api/app/services/news_service.py）

```python
def prefetch_topics(self, topics: list[str]) -> list[NewsItem]:
    if not self.settings.news_enabled:
        return []
    collected: list[NewsItem] = []
    for topic in topics[: self.settings.news_max_topics]:
        collected.extend(self.search(topic))        # ← 串行
    return _rank_news_by_recency(_dedupe_news(collected))
```

每个 `search()` 调用：
- 命中 SQLite `news_cache`（按 `topic:date` key，盘中 15min TTL）→ 立即返回；
- 否则 AkShare `stock_news_em` + 可选 `cls` + `fund_announcement_report_em`，1~3s。

5 主题串行 = 3~8s 冷态。

#### 改动

把 `prefetch_topics` 改为线程池并发，缓存写入按 topic 独立 key 已天然并发安全：

```python
from concurrent.futures import ThreadPoolExecutor

def prefetch_topics(self, topics: list[str]) -> list[NewsItem]:
    if not self.settings.news_enabled or not topics:
        return []
    limited = list(topics[: self.settings.news_max_topics])
    if len(limited) == 1:
        return _rank_news_by_recency(_dedupe_news(self.search(limited[0])))
    max_workers = min(5, len(limited))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="news-prefetch") as executor:
        results = list(executor.map(self.search, limited))
    collected: list[NewsItem] = []
    for items in results:
        collected.extend(items)
    return _rank_news_by_recency(_dedupe_news(collected))
```

`search()` 内部 AkShare 调用各自独立、`save_cached_news` 走 `INSERT OR REPLACE` 并发安全，SQLite 在写入路径仅短锁。

#### 收益

- 冷路径 **省 2~5s**（5 主题并发 → 单主题 RTT ~1.5s 为瓶颈）；
- 日报 + 荐基均受益。

#### 风险与缓解

- **风险：** AkShare `stock_news_em` 在 5 并发下被限流。
  **缓解：** AkShare 经验上对 5 并发健康；若实测出问题，留 env `FUND_AI_NEWS_PREFETCH_WORKERS`（默认 5，可降到 2），但本期不引入除非 fail。
- **风险：** SQLite 写并发死锁。
  **缓解：** `news_cache.save_cached_news` 已用短事务，且 `_connect()` 是 per-call connection；并发 5 路在 PRAGMA 默认下不会锁死，但若出现可加 `PRAGMA busy_timeout`——本期先不动。

#### 测试

`tests/test_news_service.py`：
1. `test_prefetch_topics_runs_in_parallel`：注入 `search` 模拟函数 sleep 0.2s × 5 topics，验证总耗时 < 0.5s（参考 `test_fetch_concurrency.py::test_map_holdings_concurrently_runs_in_parallel`）。
2. `test_prefetch_topics_preserves_dedup_and_ranking`：并发模式下 dedupe + recency rank 仍正确。
3. `test_prefetch_topics_single_topic_no_pool`：单主题直接调，不起线程池。

---

### F4. `fund_nav_cache` 拉取周期对齐，跨场景复用

#### 现状

| 调用点 | trading_days |
|---|---|
| `holding_intraday_warmup.warm_fund_nav_histories`（持仓详情弹窗预热） | **252** |
| `holding_detail_service` 直拉 | 252 |
| `FundDataService.get_snapshots_with_nav_trends`（日报） | `settings.nav_trend_days = 66` |
| `discovery_candidate_pool.enrich_candidates`（荐基） | 66 |
| `dip_drop_scanner._nav_summary_for_code`（大跌雷达） | 66 |

`fund_nav_cache` key 为 `fund:nav:v1:{code}:{days}`——66 与 252 不共享。用户已经点开过基金详情，预热好的 252 缓存对日报/荐基**毫无价值**。

#### 改动

把日报/荐基/大跌雷达侧的 `trading_days=66` 统一改为 **252**，与详情预热对齐：

1. `apps/api/app/config.py` `nav_trend_days: int = 66` → **`nav_trend_days: int = 252`**。
2. `apps/api/app/services/discovery_candidate_pool.py::enrich_candidates` 中 `trading_days=66` → `252`。
3. `apps/api/app/services/dip_drop_scanner.py::_nav_summary_for_code` 中 `trading_days=66` → `252`。

`summarize_nav_history` 接收完整 `FundNavHistory.points`，已经根据 `period_change_percent`（区间内涨跌幅）与 `recent_5d_change_percent`（近5日）分别取首尾——**输入更长序列只会让区间统计窗口更长**，需要确认这是否影响 LLM 决策：

- `period_change_percent`：从「66 日区间」变成「252 日区间」涨跌幅。**这是个事实变化**，需要修正：在 `summarize_nav_history` 中保留近 N 个交易日做摘要（N 取 `settings.nav_trend_window`，默认仍 66），仅净值序列拉满 252 供未来扩展。
- `recent_nav_series` cap 5 点喂 LLM，不受影响。
- `recent_5d_*` 永远取末尾 5/6 点，不受影响。
- `distance_from_high_percent` / `distance_from_low_percent`：当前是「整段区间高/低点」。改为 252 区间后变成「过去 1 年高/低距离」，**这反而更合理**（短线给宏观参考），但需要 LLM prompt 同步标注。

#### 具体方案（保守）

为了不动 LLM 决策口径，**净值拉 252 → `summarize_nav_history` 内部仅用尾部 `window_days=66` 计算摘要**：

```python
# apps/api/app/services/nav_trend_summary.py
def summarize_nav_history(
    history: FundNavHistory | None,
    *,
    recent_sample: int = 8,
    window_days: int = 66,    # 新增，默认 66
) -> dict | None:
    ...
    points = history.points
    if window_days and len(points) > window_days:
        points = points[-window_days:]   # 仅摘要时窗口化
    navs = [point.nav for point in points]
    # 其余逻辑不变
```

调用方 `FundDataService.get_snapshots_with_nav_trends` 把 `settings.nav_trend_days` 改作 `window_days` 传入，AkShare 拉取改为 252（或新 setting `nav_cache_pull_days = 252`）：

```python
# fund_data.py
def get_snapshots_with_nav_trends(self, holdings, *, trading_days=None):
    settings = get_settings()
    pull_days = trading_days if trading_days is not None else settings.nav_cache_pull_days  # 默认 252
    window_days = settings.nav_trend_window  # 默认 66
    ...
    trends[holding.fund_code] = summarize_nav_history(trend, recent_sample=sample, window_days=window_days)
```

新增两个 setting：

```python
nav_cache_pull_days: int = 252   # 与 holding detail 预热对齐，复用缓存
nav_trend_window: int = 66       # 喂 LLM 摘要用的窗口，保持现状
# 旧 nav_trend_days 标记 deprecated，临时映射到 nav_cache_pull_days 以兼容外部 env
```

`enrich_candidates` 和 `_nav_summary_for_code` 同样改用 `pull_days=252, window_days=66`。

#### 收益

- 用户**已经点开过持仓详情**的基金：日报/荐基命中已有缓存，**省 1~3s/只基金**。N 只持仓累计可观。
- 用户未点开任何基金：冷拉 252 vs 66 网络 KB 级别差异忽略。

#### 风险与缓解

- **风险：** `summarize_nav_history` 改动影响现有荐基/大跌雷达调用方传 `recent_sample=5`。
  **缓解：** 新增 `window_days` 是 keyword-only 参数，默认值即旧行为，所有现有调用无需改。
- **风险：** 252 天净值 JSON 体积变大，sector_spot_cache（SQLite）每次写多 ~10KB。
  **缓解：** 全用户共享缓存，单只基金一份；可接受。
- **风险：** `period_change_percent` 字段含义不变（仍是 window_days 区间），保持 LLM 决策口径。
  **缓解：** 改动属于「内部拉更长，摘要不变」，对外语义零变化。

#### 测试

`tests/test_fund_data.py`、`tests/test_nav_trend_summary.py`：
1. `test_summarize_nav_window_caps_to_66_days`：构造 100 日点序列，验证 `period_change_percent` 与最后 66 日相同。
2. `test_summarize_nav_window_overrides`：传入 `window_days=30` 验证窗口生效。
3. `test_get_snapshots_uses_252_day_pull`：注入 fake `fetch_fund_nav_history` 验证 `trading_days=252` 传入。
4. `test_cache_reuse_across_holdings_and_discovery`：先预热 252，再调日报路径，验证不再调 AkShare（共享缓存命中）。

---

### F5（附录·不实现）：阶段 3 前端 UX 调研存档

为阶段 3「前端骨架卡 + 流式渲染」存档轻量竞品 takeaway，本期不实现。

| 产品 | 流式粒度 | 进度反馈 | 等待行为 |
|---|---|---|---|
| ChatGPT Deep Research | 阶段卡片 + token 级最终报告 | 右侧 sidebar 实时显示 steps + sources，2026/02 支持中途追加 prompt | 鼓励 "step away…notification when complete" |
| Perplexity Deep Research | 阶段卡片为主，最终报告段落流式 | 可视化 research plan + sources + step 计数 | 云端跑、可切 tab、支持 PDF export |
| Notion AI | token 级 inline streaming | 无 stage label，光标即进度 | 期间可编辑其他区域、随时 Stop |

**5 条 takeaway（来源已记录）：**
1. 先骨架后填充（Perplexity 范式）
2. 暴露「思考过程摘要」而不只是 stage label（ChatGPT Deep Research）
3. 承诺异步、允许离开 + 浏览器通知 + 红点（ChatGPT Deep Research）
4. 中途可追加/打断（ChatGPT 2026/02 update）
5. 最终段落 token 级流式 + 可取消（Notion AI）

来源：
- https://openai.com/index/introducing-deep-research/
- https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research
- https://www.notion.com/help/guides/notion-ai-for-docs

---

## 4. 预期收益汇总（基于代码结构估算）

| 场景 | 现在 | 阶段 1 后 |
|---|---|---|
| 日报 深度 + 冷缓存 | 90~150s | **60~110s**（-25~40s） |
| 日报 深度 + 热缓存 | 50~80s | **40~65s**（-10~15s） |
| 日报 快速 + 热缓存 | 25~50s | **20~40s**（-5~10s） |
| 荐基 深度 + 冷缓存 | 60~110s | **40~80s**（-15~30s） |
| 荐基 深度 + 热缓存 | 35~60s | **25~45s**（-10~15s） |

实测以用户跑的 `JobStatusFloat` stage 时间戳为准。

---

## 5. 实施顺序（写 implementation plan 时再细化）

按风险递增 + 收益递减排序：

1. **F2** 接 `fund_rank_cache`（最简单，3 行 import + 默认值修改 + 测试）
2. **F3** `NewsService` 并发（小心写测试，与 `test_fetch_concurrency.py` 对齐 pattern）
3. **F1** `report_judge` facts 复用（要改签名 + 删两段 facts 重算 + 改测试）
4. **F4** NAV 缓存对齐（新增 settings + `summarize_nav_history` 加 window 参数，最大改动）

每项独立可测、可单独 PR，互不依赖（F4 也独立于 F1~F3）。

---

## 6. 兼容性 & 回滚

- 所有 4 项改动都是**内部数据流优化**，不改 API schema、不改 DB schema、不改前端契约。
- F1 改 `judge_parsed_report` 签名是仓内私函数，无外部消费者。
- F4 新增 `nav_cache_pull_days` / `nav_trend_window` settings，未配置时默认值即新行为；旧 `nav_trend_days` env 仍兼容（映射到 `nav_cache_pull_days`），过渡期 1 个版本。
- 任何一项可独立 revert：F1~F3 单 commit、F4 单 commit。

---

## 7. 后续阶段（不在本 spec 内）

- **阶段 2：** DeepSeek 流式输出 + SSE 前端 token 渲染（依赖：阶段 1 实测时间戳）
- **阶段 3：** 前端骨架卡 + 阶段进度卡片化（参考附录 F5 竞品 takeaway）

每阶段独立 spec → plan → 实现循环。
