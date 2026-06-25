# 日报/推荐报告管线提速 · 阶段 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不动 LLM 调用与前端 UI 前提下，消除日报/荐基管线里 facts 重算、未命中共享缓存、串行新闻拉取、净值缓存分轨四个冗余点，目标热路径省 2~4s、冷路径省 10~30s。

**Architecture:** 后端 Python 内部数据流优化——签名调整、默认值替换、并发改造、缓存窗口对齐；不引入新缓存类型、不改 API/DB schema、不改前端契约。每项独立可测、可独立 revert。

**Tech Stack:** Python 3.11+ / FastAPI / pytest / pytest-xdist（CI 并发）/ ThreadPoolExecutor / pydantic-settings

**Spec:** `docs/superpowers/specs/2026-06-25-report-pipeline-speedup-phase1-design.md`

**Order (风险递增 + 收益递减):** F2 → F3 → F1 → F4

---

## 任务总览

| 任务 | 范围 | 收益估算 | 风险 |
|---|---|---|---|
| 1 | F2 荐基/大跌池接 `fund_rank_cache` | 热路径 -1~3s | 极低 |
| 2 | F3 `NewsService.prefetch_topics` 主题并发 | 冷路径 -2~5s | 低 |
| 3 | F1 `report_judge` 复用 facts，消除 2 次重算 | 深度 -5~10s / 快速 -1~3s | 低（改私函数签名） |
| 4 | F4 `fund_nav_cache` 拉取周期对齐 252，摘要窗口 66 | 已开过详情的基金 -1~3s/只 | 中（多 setting + 摘要 window 参数） |

---

## Task 1: F2 — 荐基 / 大跌池接入 `fund_rank_cache`

**Files:**
- Modify: `apps/api/app/services/discovery_candidate_pool.py:15`（import 与默认 fetcher）
- Modify: `apps/api/app/services/dip_drop_scanner.py:9-10, 102`（import 与默认 fetcher）
- Create: `apps/api/tests/test_discovery_candidate_pool_cache.py`

---

- [ ] **Step 1: 写 F2 失败测试 — 验证 `build_candidate_pool` 默认走 `fund_rank_cache`**

Create `apps/api/tests/test_discovery_candidate_pool_cache.py`:

```python
"""F2 回归：荐基候选池默认 fetcher 接 fund_rank_cache（共享 1h 缓存）。"""

from __future__ import annotations

from unittest.mock import patch

from app.services import discovery_candidate_pool


def test_build_candidate_pool_uses_rank_cache_by_default():
    """默认不显式注入 fetcher 时，应调用 fund_rank_cache.fetch_open_fund_rank_cached，
    而不是直接走 akshare_subprocess.fetch_open_fund_rank。"""
    with (
        patch(
            "app.services.fund_rank_cache.fetch_open_fund_rank_cached",
            return_value=[],
        ) as cached,
        patch(
            "app.services.akshare_subprocess.fetch_open_fund_rank",
            return_value=[],
        ) as raw,
        patch(
            "app.services.akshare_subprocess.fetch_new_fund_offerings",
            return_value=[],
        ),
    ):
        discovery_candidate_pool.build_candidate_pool(target_sectors=["半导体"])

    assert cached.called, "应走 fund_rank_cache.fetch_open_fund_rank_cached"
    assert not raw.called, "不应直调 akshare_subprocess.fetch_open_fund_rank"


def test_build_dip_pool_for_sectors_uses_rank_cache_by_default():
    from app.services import dip_drop_scanner

    with (
        patch(
            "app.services.fund_rank_cache.fetch_open_fund_rank_cached",
            return_value=[],
        ) as cached,
        patch(
            "app.services.akshare_subprocess.fetch_open_fund_rank",
            return_value=[],
        ) as raw,
    ):
        dip_drop_scanner.build_dip_pool_for_sectors(
            target_sectors=["半导体"],
            lookback_days=5,
            min_drop_percent=3.0,
        )

    assert cached.called, "build_dip_pool_for_sectors 应走 fund_rank_cache"
    assert not raw.called
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd apps/api && python -m pytest tests/test_discovery_candidate_pool_cache.py -v
```

Expected: 2 测试 FAIL，AssertionError "应走 fund_rank_cache.fetch_open_fund_rank_cached"。

- [ ] **Step 3: 修改 `discovery_candidate_pool.py` 默认 fetcher**

Edit `apps/api/app/services/discovery_candidate_pool.py`:

```python
# 旧 line 15:
from app.services.akshare_subprocess import fetch_new_fund_offerings, fetch_open_fund_rank
```

改为：

```python
from app.services.akshare_subprocess import fetch_new_fund_offerings
from app.services.fund_rank_cache import fetch_open_fund_rank_cached
```

然后 `build_candidate_pool` 函数签名（line 22-32）的默认值：

```python
def build_candidate_pool(
    target_sectors: list[str],
    *,
    exclude_codes: set[str] | None = None,
    fund_type_preference: str = "any",
    selection_strategy: SelectionStrategy = "balanced",
    per_sector: int = _PER_SECTOR,
    pool_cap: int = _POOL_CAP,
    fetch_rank=fetch_open_fund_rank_cached,
    fetch_new_funds=fetch_new_fund_offerings,
) -> list[dict]:
```

- [ ] **Step 4: 修改 `dip_drop_scanner.py` 默认 fetcher**

Edit `apps/api/app/services/dip_drop_scanner.py`:

```python
# 旧 line 9-10:
from app.services.akshare_subprocess import fetch_open_fund_rank, fetch_open_fund_rank_worst_recent
```

改为：

```python
from app.services.akshare_subprocess import fetch_open_fund_rank_worst_recent
from app.services.fund_rank_cache import fetch_open_fund_rank_cached
```

然后函数 `build_dip_pool_for_sectors`（line 102 附近）默认值：

```python
    fetch_rank=fetch_open_fund_rank_cached,
```

> `build_dip_pool_worst_recent`（line 167 附近）继续走 `fetch_open_fund_rank_worst_recent`，不改——worst_recent 暂无缓存层，本期范围外。

- [ ] **Step 5: 跑 F2 测试确认通过**

Run:
```bash
cd apps/api && python -m pytest tests/test_discovery_candidate_pool_cache.py -v
```

Expected: 2 PASS。

- [ ] **Step 6: 跑相关回归测试，确保没破坏现有用例**

Run:
```bash
cd apps/api && python -m pytest tests/test_fetch_concurrency.py tests/test_discovery_candidate_pool_cache.py -v
```

Expected: 全 PASS。`test_fetch_concurrency.py::test_enrich_candidates_preserves_order` 仍要保持通过——它注入 `fetch_rank` 显式，与默认值修改无关。

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/discovery_candidate_pool.py \
        apps/api/app/services/dip_drop_scanner.py \
        apps/api/tests/test_discovery_candidate_pool_cache.py
git commit -m "$(cat <<'EOF'
perf(discovery): 荐基/大跌池接 fund_rank_cache，复用因子分模块 1h 共享缓存

build_candidate_pool / build_dip_pool_for_sectors 默认 fetcher 从 akshare_subprocess.fetch_open_fund_rank
替换为 fund_rank_cache.fetch_open_fund_rank_cached——与 build_factor_scores_payload 走同一份缓存，
避免一次请求里两份排行榜数据各自走子进程。热路径省 1~3s。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: F3 — `NewsService.prefetch_topics` 主题并发

**Files:**
- Modify: `apps/api/app/services/news_service.py:76-83`（`prefetch_topics` 方法）
- Create: `apps/api/tests/test_news_service_prefetch.py`

---

- [ ] **Step 1: 写 F3 失败测试 — 并发计时 + dedup/ranking**

Create `apps/api/tests/test_news_service_prefetch.py`:

```python
"""F3 回归：NewsService.prefetch_topics 多主题并发拉取。"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from app.models import NewsItem
from app.services.news_service import NewsService


def _make_item(topic: str, title: str, today: bool = True) -> NewsItem:
    return NewsItem(
        topic=topic,
        title=title,
        published_at="2026-06-25 10:00",
        source="test",
        is_today=today,
    )


def test_prefetch_topics_runs_topics_in_parallel():
    """5 个主题每个 sleep 0.2s，并发执行总耗时应远小于串行 1s。"""
    service = NewsService()

    def slow_search(topic: str, limit: int | None = None):
        time.sleep(0.2)
        return [_make_item(topic, f"{topic} title")]

    topics = ["半导体", "商业航天", "新能源车", "医药", "银行"]

    with patch.object(service, "search", side_effect=slow_search):
        start = time.monotonic()
        result = service.prefetch_topics(topics)
        elapsed = time.monotonic() - start

    assert elapsed < 0.6, f"并发执行应远快于串行 1s，实际 {elapsed:.2f}s"
    titles = [item.title for item in result]
    assert set(titles) == {f"{t} title" for t in topics}


def test_prefetch_topics_dedupes_and_ranks_today_first():
    service = NewsService()

    def fake_search(topic, limit=None):
        if topic == "半导体":
            return [
                _make_item("半导体", "重复标题", today=False),
                _make_item("半导体", "新标题", today=True),
            ]
        return [_make_item(topic, "重复标题", today=False)]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(["半导体", "商业航天"])

    titles_in_order = [item.title for item in result]
    # 当日新闻应排在前面（_rank_news_by_recency）
    assert titles_in_order[0] == "新标题"
    # "重复标题" 经 _dedupe_news 仅保留一次
    assert titles_in_order.count("重复标题") == 1


def test_prefetch_topics_single_topic_skips_threadpool():
    service = NewsService()
    invoked_in_main_thread = {"flag": False}
    main_ident = threading.get_ident()

    def fake_search(topic, limit=None):
        invoked_in_main_thread["flag"] = (threading.get_ident() == main_ident)
        return [_make_item(topic, "x")]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(["半导体"])

    assert invoked_in_main_thread["flag"], "单主题应不起线程池，直接主线程跑"
    assert result[0].title == "x"


def test_prefetch_topics_disabled_returns_empty():
    service = NewsService()
    with patch.object(service.settings, "news_enabled", False, create=True):
        assert service.prefetch_topics(["半导体"]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd apps/api && python -m pytest tests/test_news_service_prefetch.py -v
```

Expected:
- `test_prefetch_topics_runs_topics_in_parallel` FAIL（串行 ~1s，断言 <0.6s）
- 其他 dedup/single 通过现状串行实现也能 pass；single 测试可能 PASS（现状本就主线程串行）；为简化逐项验证，确保并行测试必然 FAIL 即可

- [ ] **Step 3: 改 `NewsService.prefetch_topics` 为线程池并发**

Edit `apps/api/app/services/news_service.py`:

旧 line 76-83:

```python
    def prefetch_topics(self, topics: list[str]) -> list[NewsItem]:
        if not self.settings.news_enabled:
            return []

        collected: list[NewsItem] = []
        for topic in topics[: self.settings.news_max_topics]:
            collected.extend(self.search(topic))
        return _rank_news_by_recency(_dedupe_news(collected))
```

替换为：

```python
    def prefetch_topics(self, topics: list[str]) -> list[NewsItem]:
        if not self.settings.news_enabled or not topics:
            return []

        limited = list(topics[: self.settings.news_max_topics])
        if len(limited) == 1:
            return _rank_news_by_recency(_dedupe_news(self.search(limited[0])))

        from concurrent.futures import ThreadPoolExecutor

        max_workers = min(5, len(limited))
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="news-prefetch",
        ) as executor:
            results = list(executor.map(self.search, limited))

        collected: list[NewsItem] = []
        for items in results:
            collected.extend(items)
        return _rank_news_by_recency(_dedupe_news(collected))
```

- [ ] **Step 4: 跑 F3 测试确认通过**

Run:
```bash
cd apps/api && python -m pytest tests/test_news_service_prefetch.py -v
```

Expected: 4 PASS。

- [ ] **Step 5: 跑相关回归测试**

Run:
```bash
cd apps/api && python -m pytest tests/ -k "news or prefetch" -v
```

Expected: 全 PASS。若有 `test_market_shared_cache.py`、`test_report_chat.py` 等触及 NewsService，确认无 regression。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/news_service.py \
        apps/api/tests/test_news_service_prefetch.py
git commit -m "$(cat <<'EOF'
perf(news): NewsService.prefetch_topics 多主题并发拉取

原 for-loop 串行 5 个主题 × 1~3s/主题 = 3~8s 冷态；改为 ThreadPoolExecutor.map
最多 5 worker，瓶颈降到单主题 RTT。news_cache 按 topic:date 独立 key，并发写安全。
单主题直接主线程跑，不起线程池。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: F1 — `report_judge` 复用 facts，消除 2 次重算

**Files:**
- Modify: `apps/api/app/services/report_judge.py:22-133`（`judge_parsed_report` / `_rule_judge` / `_llm_judge` 签名 + 删 facts 重算）
- Modify: `apps/api/app/services/deepseek_client.py:147-148`（传 facts）
- Create: `apps/api/tests/test_report_judge_facts_reuse.py`

---

- [ ] **Step 1: 写 F1 失败测试 — 注入异常验证 build_analysis_facts 不被调**

Create `apps/api/tests/test_report_judge_facts_reuse.py`:

```python
"""F1 回归：judge_parsed_report 复用上游 facts，不再调用 build_analysis_facts。"""

from __future__ import annotations

from unittest.mock import patch

from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_runtime import resolve_analysis_runtime
from app.config import get_settings


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10000,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=30,
            expected_investment_amount=100000,
        ),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.0,
        suggested_action="watch",
        alerts=[],
    )


def _fake_facts() -> dict:
    return {
        "readonly": True,
        "instruction": "fake",
        "portfolio": {
            "weighted_return_percent": 1.0,
            "risk_level": "medium",
            "suggested_action": "watch",
            "concentration_limit_percent": 30,
        },
        "holdings": [
            {"fund_code": "519674", "weight_percent": 50.0}
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        "alerts": [],
        "news": {},
    }


def test_judge_does_not_call_build_analysis_facts():
    """当调用方传入 facts 时，judge 不应再调用 build_analysis_facts。"""
    from app.services import report_judge

    parsed = {
        "title": "test",
        "summary": "ok",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "银河创新成长", "action": "观察"}
        ],
        "caveats": [],
    }
    snapshots = [FundSnapshot(fund_code="519674", fund_name="银河创新成长", source="test")]
    runtime = resolve_analysis_runtime(get_settings(), "fast")

    with patch(
        "app.services.report_judge.build_analysis_facts",
        side_effect=AssertionError("build_analysis_facts 不应再被调用"),
    ):
        out, meta = report_judge.judge_parsed_report(
            parsed, _request(), _risk(), snapshots, runtime, facts=_fake_facts()
        )

    assert out["fund_recommendations"][0]["fund_code"] == "519674"
    assert meta["rule_judge"] is True


def test_rule_judge_respects_concentration_using_provided_facts():
    """超集中度的持仓建议加仓 → 应被改写为减仓评估，且不重算 facts。"""
    from app.services import report_judge

    facts = _fake_facts()
    facts["holdings"] = [{"fund_code": "519674", "weight_percent": 80.0}]  # 超 30%

    parsed = {
        "title": "test",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "x", "action": "分批加仓"}
        ],
    }
    snapshots = [FundSnapshot(fund_code="519674", fund_name="x", source="test")]
    runtime = resolve_analysis_runtime(get_settings(), "fast")

    with patch(
        "app.services.report_judge.build_analysis_facts",
        side_effect=AssertionError("不应再调"),
    ):
        out, _meta = report_judge.judge_parsed_report(
            parsed, _request(), _risk(), snapshots, runtime, facts=facts
        )

    assert out["fund_recommendations"][0]["action"] == "减仓评估"
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd apps/api && python -m pytest tests/test_report_judge_facts_reuse.py -v
```

Expected: 2 FAIL — `TypeError: judge_parsed_report() got an unexpected keyword argument 'facts'`。

- [ ] **Step 3: 改 `judge_parsed_report` 接口**

Edit `apps/api/app/services/report_judge.py` line 22-42:

```python
def judge_parsed_report(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    runtime: AnalysisRuntime,
    *,
    facts: dict,
) -> tuple[dict, dict]:
    judged = _rule_judge(parsed, request, risk, facts)
    meta = {
        "rule_judge": True,
        "llm_judge_attempted": False,
        "llm_judge_applied": False,
    }
    if runtime.mode != "deep" or not get_settings().deepseek_configured:
        return judged, meta
    meta["llm_judge_attempted"] = True
    reviewed = _llm_judge(judged, request, runtime, facts)
    if reviewed is not judged and reviewed.get("fund_recommendations"):
        meta["llm_judge_applied"] = True
        return reviewed, meta
    return judged, meta
```

- [ ] **Step 4: 改 `_rule_judge` 复用 facts**

Edit `apps/api/app/services/report_judge.py` line 45-87:

```python
def _rule_judge(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    facts: dict,
) -> dict:
    weight_by_code = {
        item["fund_code"]: item["weight_percent"]
        for item in facts.get("holdings") or []
    }
    allowed = set(facts.get("allowed_actions") or [])

    raw_recs = parsed.get("fund_recommendations")
    if not isinstance(raw_recs, list):
        return parsed

    fixed_recs: list[dict] = []
    for entry in raw_recs:
        if not isinstance(entry, dict):
            continue
        copy = dict(entry)
        action = normalize_action_text(str(copy.get("action", "观察")))
        if action not in allowed:
            action = "观察"
        code = str(copy.get("fund_code", "")).strip()
        if risk.suggested_action == "risk_review" and _action_bucket(action) >= 3:
            action = "暂停追涨"
        if code in weight_by_code and weight_by_code[code] > request.profile.concentration_limit_percent:
            if _action_bucket(action) >= 3:
                action = "减仓评估"
        copy["action"] = action
        fixed_recs.append(copy)

    copy_parsed = dict(parsed)
    copy_parsed["fund_recommendations"] = fixed_recs

    summary = str(copy_parsed.get("summary", ""))
    if risk.suggested_action == "risk_review" and "加仓" in summary and "不宜" not in summary:
        copy_parsed["summary"] = (
            f"{summary}\n\n（系统复核：组合处于风险复核状态，今日不宜新增加仓。）"
        ).strip()

    return copy_parsed
```

- [ ] **Step 5: 改 `_llm_judge` 复用 facts**

Edit `apps/api/app/services/report_judge.py` line 90-133:

```python
def _llm_judge(
    parsed: dict,
    request: AnalysisRequest,
    runtime: AnalysisRuntime,
    facts: dict,
) -> dict:
    settings = get_settings()
    payload = {
        "facts": facts,
        "draft_report": parsed,
        "task": (
            "你是审校员。对照 facts 检查 draft_report，修正与数字/风控矛盾之处。"
            "仅输出完整 JSON，结构同 draft_report（title、summary、fund_recommendations、caveats）。"
            "不得放宽风控：risk_review 时禁止加仓类 action。"
        ),
    }
    try:
        response = httpx.post(
            deepseek_chat_url(settings),
            headers=deepseek_request_headers(settings),
            json={
                "model": settings.deepseek_model_fast,
                "messages": [
                    {"role": "system", "content": "你是严谨的基金日报审校员。"},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": 0.1,
                "max_tokens": min(settings.deepseek_max_tokens_report, 8000),
                "response_format": {"type": "json_object"},
            },
            timeout=deepseek_timeout(settings),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
        from app.services.deepseek_client import _parse_model_json

        reviewed = _parse_model_json(content)
        if reviewed.get("fund_recommendations"):
            return reviewed
    except Exception as exc:
        logger.warning("llm judge failed, using rule-judged report: %s", exc)
    return parsed
```

注意：`_llm_judge` 删掉了 `risk` 与 `snapshots` 入参（不再需要），相应 import 也清掉未使用项。`AnalysisRequest` 仍保留供未来扩展，且测试用例传它。

- [ ] **Step 6: 改 `deepseek_client.generate_report` 传 facts**

Edit `apps/api/app/services/deepseek_client.py` line 147-148:

```python
            progress("judging")
            parsed, judge_meta = judge_parsed_report(
                parsed, request, risk, snapshots, runtime,
                facts=analysis_bundle.facts,
            )
```

- [ ] **Step 7: 跑 F1 测试确认通过**

Run:
```bash
cd apps/api && python -m pytest tests/test_report_judge_facts_reuse.py -v
```

Expected: 2 PASS。

- [ ] **Step 8: 跑全量相关回归**

Run:
```bash
cd apps/api && python -m pytest tests/ -k "judge or analysis_payload or report" -v
```

Expected: 全 PASS。`test_analysis_payload_bundle.py::test_build_user_payload_reuses_analysis_bundle` 仍要通过。

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/services/report_judge.py \
        apps/api/app/services/deepseek_client.py \
        apps/api/tests/test_report_judge_facts_reuse.py
git commit -m "$(cat <<'EOF'
perf(report): judge_parsed_report 复用上游 facts，消除 2 次 build_analysis_facts 重算

generate_report → prepare_analysis_bundle 已算一份 facts；原 report_judge 里
_rule_judge 和 _llm_judge 各自又调 build_analysis_facts (且入参缺 nav_trends/factor_scores/
risk_metrics → 与 prompt facts 字段集合不一致)。改为 judge_parsed_report 接受必填
facts kwarg，整个 judge 路径共享同一份 facts。

深度模式省 5~10s，快速模式省 1~3s；同时修复 facts 不一致风险。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: F4 — `fund_nav_cache` 拉取周期对齐 252 / 摘要窗口 66

**Files:**
- Modify: `apps/api/app/config.py:70`（settings 新增 + deprecated 兼容）
- Modify: `apps/api/app/services/nav_trend_summary.py:6-60`（新增 `window_days` 参数）
- Modify: `apps/api/app/services/fund_data.py:33-60`（拉 252、摘要传 window）
- Modify: `apps/api/app/services/discovery_candidate_pool.py:76`（trading_days 改 252）
- Modify: `apps/api/app/services/dip_drop_scanner.py:337`（trading_days 改 252）
- Create: `apps/api/tests/test_nav_trend_window.py`
- Create: `apps/api/tests/test_fund_nav_pull_days.py`

---

- [ ] **Step 1: 写 F4 摘要窗口失败测试**

Create `apps/api/tests/test_nav_trend_window.py`:

```python
"""F4 回归：summarize_nav_history window_days 在摘要时窗口化。"""

from __future__ import annotations

from app.models import FundNavHistory, FundNavPoint
from app.services.nav_trend_summary import summarize_nav_history


def _linear_history(days: int, start: float = 1.0, step: float = 0.01) -> FundNavHistory:
    """构造 days 条点序列：单位净值线性递增，便于断言区间涨跌。"""
    points = [
        FundNavPoint(
            date=f"2026-01-{i + 1:02d}",
            nav=round(start + step * i, 4),
            daily_return_percent=None,
        )
        for i in range(days)
    ]
    return FundNavHistory(
        fund_code="000001",
        fund_name="测试",
        source="akshare",
        points=points,
    )


def test_window_days_caps_to_66_by_default():
    """传 100 日 → 默认 window_days=66 → period_change 只反映末 66 日。"""
    hist = _linear_history(100)  # nav 从 1.00 线性到 1.99
    summary = summarize_nav_history(hist)  # 默认 window_days=66
    # 末 66 日：第 34 日 nav=1.34，第 100 日 nav=1.99 → (1.99/1.34 - 1)*100
    expected = round((1.99 / 1.34 - 1) * 100, 2)
    assert summary["period_days"] == 66
    assert summary["period_change_percent"] == expected


def test_window_days_explicit_override():
    """显式 window_days=30 → period_days=30。"""
    hist = _linear_history(100)
    summary = summarize_nav_history(hist, window_days=30)
    assert summary["period_days"] == 30


def test_window_days_none_keeps_full_series():
    """window_days=None → 使用全部点（向后兼容旧行为）。"""
    hist = _linear_history(100)
    summary = summarize_nav_history(hist, window_days=None)
    assert summary["period_days"] == 100


def test_window_smaller_than_points_unchanged():
    """点数少于 window_days → 全用，不截。"""
    hist = _linear_history(20)
    summary = summarize_nav_history(hist, window_days=66)
    assert summary["period_days"] == 20


def test_recent_5d_unaffected_by_window():
    """recent_5d_change_percent 只看末 6 点，与 window 无关。"""
    hist = _linear_history(100)
    summary = summarize_nav_history(hist, window_days=66)
    # 末 6 点：nav 从 1.94 到 1.99 → (1.99/1.94 - 1)*100
    expected_5d = round((1.99 / 1.94 - 1) * 100, 2)
    assert summary["recent_5d_change_percent"] == expected_5d
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd apps/api && python -m pytest tests/test_nav_trend_window.py -v
```

Expected: 5 FAIL — `TypeError: summarize_nav_history() got an unexpected keyword argument 'window_days'` 或 period_days=100。

- [ ] **Step 3: 给 `summarize_nav_history` 加 `window_days` 参数**

Edit `apps/api/app/services/nav_trend_summary.py`:

```python
from __future__ import annotations

from app.models import FundNavHistory, FundNavPoint


def summarize_nav_history(
    history: FundNavHistory | None,
    *,
    recent_sample: int = 8,
    window_days: int | None = 66,
) -> dict | None:
    if history is None or not history.points:
        return None
    if history.source in {"unavailable", "error"}:
        return None

    points = history.points
    if window_days and len(points) > window_days:
        points = points[-window_days:]

    navs = [point.nav for point in points]
    high_nav = max(navs)
    low_nav = min(navs)
    latest = points[-1]
    start = points[0]

    period_change = None
    if start.nav > 0:
        period_change = round((latest.nav / start.nav - 1) * 100, 2)

    recent_5d_change = None
    if len(history.points) >= 6 and history.points[-6].nav > 0:
        # recent_5d 看真实最后 6 点，不被 window 影响
        recent_5d_change = round((latest.nav / history.points[-6].nav - 1) * 100, 2)

    distance_from_high = None
    if high_nav > 0:
        distance_from_high = round((latest.nav / high_nav - 1) * 100, 2)

    distance_from_low = None
    if low_nav > 0:
        distance_from_low = round((latest.nav / low_nav - 1) * 100, 2)

    sample_size = max(3, min(recent_sample, len(history.points)))
    recent_nav_series = [
        {"date": point.date, "nav": round(point.nav, 4)}
        for point in history.points[-sample_size:]
    ]
    recent_5d_daily_change_percent = _recent_daily_nav_changes(
        history.points, max_days=5
    )

    return {
        "period_days": len(points),
        "period_change_percent": period_change,
        "recent_5d_change_percent": recent_5d_change,
        "recent_5d_daily_change_percent": recent_5d_daily_change_percent,
        "latest_nav": latest.nav,
        "latest_date": latest.date,
        "high_nav": round(high_nav, 4),
        "low_nav": round(low_nav, 4),
        "distance_from_high_percent": distance_from_high,
        "distance_from_low_percent": distance_from_low,
        "trend_label": _trend_label(period_change, recent_5d_change),
        "source": history.source,
        "recent_nav_series": recent_nav_series,
    }


def _trend_label(
    period_change: float | None,
    recent_5d_change: float | None,
) -> str:
    if period_change is None:
        return "数据不足"

    if period_change >= 5:
        base = "区间上升"
    elif period_change <= -5:
        base = "区间下行"
    elif period_change >= 1.5:
        base = "温和上行"
    elif period_change <= -1.5:
        base = "温和下行"
    else:
        base = "区间震荡"

    if recent_5d_change is None:
        return base

    if recent_5d_change >= 2 and (period_change or 0) < 1:
        return f"{base}，近5日走强"
    if recent_5d_change <= -2 and (period_change or 0) > -1:
        return f"{base}，近5日走弱"
    if recent_5d_change > 0 and period_change < 0:
        return f"{base}，近5日反弹"
    if recent_5d_change < 0 and period_change > 0:
        return f"{base}，近5日回落"

    return base


def _recent_daily_nav_changes(points: list[FundNavPoint], *, max_days: int = 5) -> list[float]:
    changes: list[float] = []
    start_index = max(1, len(points) - max_days)
    for index in range(start_index, len(points)):
        prev = points[index - 1].nav
        curr = points[index].nav
        if prev <= 0:
            continue
        changes.append(round((curr / prev - 1) * 100, 2))
    return changes
```

> 注意：`distance_from_high/low` 现在是基于窗口内的高低点（66 日），与现状（输入即 66 日，效果相同）等价；`recent_nav_series` 用 `history.points[-sample_size:]` 取自真实尾部，不被 window 影响——保留与现状一致的喂 LLM 行为。

- [ ] **Step 4: 跑 window 测试确认通过**

Run:
```bash
cd apps/api && python -m pytest tests/test_nav_trend_window.py -v
```

Expected: 5 PASS。

- [ ] **Step 5: 新增 settings `nav_cache_pull_days` / `nav_trend_window`，deprecated 兼容**

Edit `apps/api/app/config.py` 第 70 行附近：

```python
# 旧:
    nav_trend_days: int = 66

# 新（替换该行 + 上下文）:
    # 拉满 252 让日报/荐基与持仓详情弹窗预热共享 fund_nav_cache（key: code+days）。
    # 旧 nav_trend_days env 仍兼容（fallback 映射到 nav_cache_pull_days），过渡期一版。
    nav_cache_pull_days: int = 252
    nav_trend_window: int = 66
    nav_trend_recent_sample: int = 8
```

把原本同位置的 `nav_trend_recent_sample: int = 8` 保留（已经有）。

新增 property 兼容旧 env：

```python
    @property
    def nav_trend_days(self) -> int:
        """Deprecated: 旧 env FUND_AI_NAV_TREND_DAYS 仍兼容，映射到 nav_cache_pull_days。"""
        return self.nav_cache_pull_days
```

如果旧代码或测试还有 `settings.nav_trend_days` 读取，该 property 即可兼容。grep 确认：

Run:
```bash
cd apps/api && grep -rn "nav_trend_days" app/ tests/
```

预期仅 `fund_data.py:40` 引用，下一步会改。

- [ ] **Step 6: 改 `FundDataService.get_snapshots_with_nav_trends` 拉 252、摘要传 window**

Edit `apps/api/app/services/fund_data.py` line 33-60:

```python
class FundDataService:
    def get_snapshots_with_nav_trends(
        self,
        holdings: list[Holding],
        *,
        trading_days: int | None = None,
    ) -> tuple[list[FundSnapshot], dict[str, dict]]:
        settings = get_settings()
        days = trading_days if trading_days is not None else settings.nav_cache_pull_days
        sample = settings.nav_trend_recent_sample
        window = settings.nav_trend_window

        results = _map_holdings_concurrently(
            holdings,
            lambda holding: self._snapshot_and_trend_for_holding(
                holding, trading_days=days
            ),
        )

        snapshots: list[FundSnapshot] = []
        trends: dict[str, dict] = {}
        for holding, (snapshot, trend) in zip(holdings, results):
            snapshots.append(snapshot)
            if trend is not None:
                trends[holding.fund_code] = summarize_nav_history(
                    trend, recent_sample=sample, window_days=window
                ) or {}
        return snapshots, trends
```

- [ ] **Step 7: 改 `discovery_candidate_pool.enrich_candidates` 拉 252**

Edit `apps/api/app/services/discovery_candidate_pool.py` line 76:

```python
# 旧:
        snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=66)

# 新:
        snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=252)
```

把同函数下 `summarize_nav_history(trend, recent_sample=5)` 改成显式传 window：

```python
        if trend is not None and getattr(trend, "points", None):
            from app.services.nav_trend_summary import summarize_nav_history

            row["nav_trend"] = summarize_nav_history(
                trend, recent_sample=5, window_days=66
            )
```

- [ ] **Step 8: 改 `dip_drop_scanner._nav_summary_for_code` 拉 252**

Edit `apps/api/app/services/dip_drop_scanner.py` line 337:

```python
# 旧:
    _snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=66)

# 新:
    _snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=252)
```

`summarize_nav_history(trend, recent_sample=5)` 同样补显式 window:

```python
    return summarize_nav_history(trend, recent_sample=5, window_days=66)
```

- [ ] **Step 9: 写 pull_days 集成测试**

Create `apps/api/tests/test_fund_nav_pull_days.py`:

```python
"""F4 回归：FundDataService 拉满 252 + 摘要窗口 66，复用持仓详情预热缓存。"""

from __future__ import annotations

from unittest.mock import patch

from app.config import refresh_settings
from app.models import Holding
from app.services.fund_data import FundDataService


def test_get_snapshots_uses_nav_cache_pull_days_default(monkeypatch):
    """默认 trading_days=None 时应取 settings.nav_cache_pull_days (默认 252)。"""
    refresh_settings()
    captured = {"trading_days": None}

    def fake_snapshot_and_trend(self, holding, *, trading_days):
        captured["trading_days"] = trading_days
        from app.models import FundNavHistory, FundSnapshot

        return (
            FundSnapshot(fund_code=holding.fund_code, fund_name="", source="test"),
            FundNavHistory(
                fund_code=holding.fund_code,
                fund_name="",
                source="akshare",
                points=[],
            ),
        )

    monkeypatch.setattr(
        "app.services.fund_data.FundDataService._snapshot_and_trend_for_holding",
        fake_snapshot_and_trend,
    )

    FundDataService().get_snapshots_with_nav_trends(
        [Holding(fund_code="519674", fund_name="x", holding_amount=10000)]
    )

    assert captured["trading_days"] == 252


def test_summary_uses_nav_trend_window(monkeypatch):
    """摘要应传 window_days=settings.nav_trend_window (默认 66)。"""
    from app.models import FundNavHistory, FundNavPoint, FundSnapshot

    points = [
        FundNavPoint(date=f"2026-01-{i + 1:02d}", nav=1.0 + i * 0.01)
        for i in range(100)
    ]
    hist = FundNavHistory(
        fund_code="519674",
        fund_name="x",
        source="akshare",
        points=points,
    )

    def fake_snapshot(self, holding, *, trading_days):
        return FundSnapshot(fund_code=holding.fund_code, fund_name="", source="test"), hist

    monkeypatch.setattr(
        "app.services.fund_data.FundDataService._snapshot_and_trend_for_holding",
        fake_snapshot,
    )

    _snapshots, trends = FundDataService().get_snapshots_with_nav_trends(
        [Holding(fund_code="519674", fund_name="x", holding_amount=10000)]
    )

    summary = trends["519674"]
    assert summary["period_days"] == 66  # window 已生效


def test_legacy_nav_trend_days_property_maps_to_new_setting():
    """旧 settings.nav_trend_days property 仍可读，等于 nav_cache_pull_days。"""
    from app.config import get_settings

    settings = get_settings()
    assert settings.nav_trend_days == settings.nav_cache_pull_days
```

- [ ] **Step 10: 跑 F4 集成测试**

Run:
```bash
cd apps/api && python -m pytest tests/test_nav_trend_window.py tests/test_fund_nav_pull_days.py -v
```

Expected: 8 PASS。

- [ ] **Step 11: 跑相关回归（nav / discovery / dip / fund_data）**

Run:
```bash
cd apps/api && python -m pytest tests/ -k "nav or discovery or dip or fund_data or analysis_payload" -v
```

Expected: 全 PASS。注意：
- `test_fund_nav_cache.py` 检查 `(code, days)` key——把 days 改 252 应仍命中相同 key 函数行为
- `test_analysis_payload_bundle.py` 使用 mock snapshot，不受 days 改动影响

- [ ] **Step 12: 跑全量后端单测确认无回归**

Run:
```bash
cd apps/api && python -m pytest tests/ -n auto --dist loadscope
```

Expected: 全部 PASS（参考 README 现 ~300+ 项）。

- [ ] **Step 13: Commit**

```bash
git add apps/api/app/config.py \
        apps/api/app/services/nav_trend_summary.py \
        apps/api/app/services/fund_data.py \
        apps/api/app/services/discovery_candidate_pool.py \
        apps/api/app/services/dip_drop_scanner.py \
        apps/api/tests/test_nav_trend_window.py \
        apps/api/tests/test_fund_nav_pull_days.py
git commit -m "$(cat <<'EOF'
perf(nav): fund_nav_cache 拉取周期对齐 252，摘要窗口仍 66

原 holding_intraday_warmup 预热 trading_days=252、日报/荐基/大跌雷达拉 66；
fund_nav_cache key 含 days → 两份缓存互不共享，已开过详情的基金对日报路径无价值。

改：
- summarize_nav_history 加 window_days kwarg（默认 66，保留 LLM 决策口径）
- new settings nav_cache_pull_days=252 / nav_trend_window=66
- nav_trend_days 转 property 兼容旧 env，映射到 nav_cache_pull_days
- 日报/荐基/大跌雷达调用点 trading_days 改 252，摘要传 window=66

已开过持仓详情的基金，日报/荐基命中已有缓存，省 1~3s/只。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 全量验证 + 文档同步

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`（顶部「更新记录」加 1 条）

---

- [ ] **Step 1: 跑全量后端测试**

Run:
```bash
cd apps/api && python -m pytest tests/ -n auto --dist loadscope -q
```

Expected: 全部 PASS。记录耗时。

- [ ] **Step 2: （可选）人工冒烟：本地跑一次日报，对比 stage 时间戳**

如果有真实 DeepSeek key + 测试用户：

```bash
bash scripts/dev.sh   # 启动后端 + 前端
# 在浏览器跑一次「生成日报」
# 在 JobStatusFloat 看 stage 时间戳，与之前对比，确认 fund_data / news_prefetch / judging 阶段都更短
```

如果没有真实环境，跳过——单测已覆盖逻辑层。

- [ ] **Step 3: 更新 `docs/PROJECT_CONTEXT.md` 顶部更新记录**

Edit `docs/PROJECT_CONTEXT.md` 第 7-8 行附近，在 `**更新记录：**` 列表顶部插入：

```markdown
- **日报/推荐报告管线提速 · 阶段 1（2026-06-25）：** 四项后端数据/缓存优化落地。**F2** `discovery_candidate_pool.build_candidate_pool` / `dip_drop_scanner.build_dip_pool_for_sectors` 默认 fetcher 接 `fund_rank_cache.fetch_open_fund_rank_cached`（与因子分模块共享 1h 缓存）。**F3** `NewsService.prefetch_topics` 由串行改 `ThreadPoolExecutor.map`（max=5），冷态 5 主题并发省 2~5s。**F1** `judge_parsed_report` 增加 `facts: dict` 必填 kwarg，`_rule_judge` / `_llm_judge` 不再各自重算 `build_analysis_facts`（深度模式 -5~10s、快速 -1~3s）。**F4** `nav_cache_pull_days=252` / `nav_trend_window=66`，`summarize_nav_history` 加 `window_days` 参数——拉满 252 让日报/荐基与持仓详情弹窗预热共享 `fund_nav_cache`，摘要窗口仍 66 保留 LLM 决策口径；旧 `nav_trend_days` 转 property 兼容。设计/计划：`docs/superpowers/specs/2026-06-25-report-pipeline-speedup-phase1-design.md` / `docs/superpowers/plans/2026-06-25-report-pipeline-speedup-phase1.md`。阶段 2（LLM 流式）/ 阶段 3（前端骨架卡）独立 spec 排期。
```

> 注意：保留现有「2026-06-25」其它条目不动；本条放最前。

- [ ] **Step 4: Commit 文档**

```bash
git add docs/PROJECT_CONTEXT.md \
        docs/superpowers/plans/2026-06-25-report-pipeline-speedup-phase1.md
git commit -m "$(cat <<'EOF'
docs: 同步阶段 1 管线提速落地记录，归档实施计划

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: 最终回放 — 阶段 1 收尾**

Run:
```bash
cd "$(git rev-parse --show-toplevel)" && git log --oneline -6
```

Expected: 看到 5 个本期 commit（4 个 perf + 1 个 docs），spec commit 仍在更早。

阶段 1 完成。等用户跑实测 stage 时间戳后，立阶段 2（DeepSeek 流式）与阶段 3（前端骨架卡）独立 spec。

---

## 总收益核对（实施完毕后再核）

| 场景 | 阶段 1 前 | 阶段 1 目标 | 单测能验 | 需要实测 |
|---|---|---|---|---|
| 日报 深度 + 冷 | 90~150s | 60~110s | F1/F3 局部断言 | 总耗时 |
| 日报 深度 + 热 | 50~80s | 40~65s | F1 facts 不重算 | 总耗时 |
| 日报 快速 + 热 | 25~50s | 20~40s | F1 facts 不重算 | 总耗时 |
| 荐基 深度 + 冷 | 60~110s | 40~80s | F2/F3 默认 fetcher 切换 | 总耗时 |
| 荐基 深度 + 热 | 35~60s | 25~45s | 同上 | 总耗时 |
