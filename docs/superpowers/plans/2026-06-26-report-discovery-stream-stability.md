# Report Discovery Stream Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix slow/stuck news stages and keep discovery stream progress alive when switching tabs.

**Architecture:** Backend news and summary fan-out will enforce real wall-clock budgets and degrade to partial/offline results. Frontend discovery streaming remains driven by `FundDiscoveryPanel`, but tab unmount cleanup no longer aborts the parent-held stream; explicit cancel remains the only abort path.

**Tech Stack:** FastAPI/Python services with pytest; React/Next TypeScript components with Vitest and Testing Library.

---

## File Structure

- Modify `apps/api/app/services/news_service.py`: make `prefetch_topics` return on deadline without waiting for blocked worker threads.
- Modify `apps/api/app/services/news_summarizer.py`: make topic summarization return offline fallbacks for slow topics without waiting for blocked workers.
- Modify `apps/api/tests/test_news_service_prefetch.py`: strengthen timeout regression.
- Create or modify `apps/api/tests/test_news_summarizer.py`: add summary timeout fallback regression.
- Modify `apps/web/src/components/FundDiscoveryPanel.tsx`: remove unmount abort while preserving explicit cancel.
- Create `apps/web/src/components/FundDiscoveryPanel.stream-lifecycle.test.tsx`: assert unmounting the discovery tab does not abort an active stream.

### Task 1: Backend News Prefetch Deadline

**Files:**
- Modify: `apps/api/tests/test_news_service_prefetch.py`
- Modify: `apps/api/app/services/news_service.py`

- [x] **Step 1: Write the failing test**

Add:

```python
def test_prefetch_topics_total_timeout_does_not_wait_for_blocked_workers(
    news_prefetch_enabled, monkeypatch
):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_prefetch_total_timeout_seconds", 0.05)

    def blocked_search(topic: str, limit: int | None = None):
        time.sleep(1.0)
        return [_make_item(topic, f"{topic} title")]

    topics = ["半导体", "商业航天", "新能源车", "医药", "银行"]
    with patch.object(service, "search", side_effect=blocked_search):
        start = time.monotonic()
        result = service.prefetch_topics(topics)
        elapsed = time.monotonic() - start

    assert elapsed < 0.3, f"总超时应不等待阻塞 worker，实际 {elapsed:.2f}s"
    assert result == []
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_news_service_prefetch.py::test_prefetch_topics_total_timeout_does_not_wait_for_blocked_workers -q
```

Expected: fail because current implementation waits about 1 second.

- [x] **Step 3: Implement minimal code**

Change `prefetch_topics` to create the executor manually, use `as_completed(..., timeout=remaining)`, catch timeout, then call `shutdown(wait=False, cancel_futures=True)`.

- [x] **Step 4: Verify green**

Run:

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_news_service_prefetch.py -q
```

Expected: all news prefetch tests pass.

### Task 2: Backend Topic Summary Deadline

**Files:**
- Create/Modify: `apps/api/tests/test_news_summarizer.py`
- Modify: `apps/api/app/services/news_summarizer.py`

- [x] **Step 1: Write the failing test**

Add:

```python
def test_summarize_all_topics_total_timeout_falls_back_without_waiting_for_blocked_workers(
    monkeypatch,
):
    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "a" * 32)
    monkeypatch.setattr(settings, "news_summarize", True)
    monkeypatch.setattr(settings, "news_summarize_timeout_seconds", 0.05)
    items = [
        _item("半导体", "半导体新闻"),
        _item("白酒", "白酒新闻"),
        _item("商业航天", "商业航天新闻"),
        _item("人工智能", "人工智能新闻"),
    ]

    def blocked_summary(topic, group_items, resolved):
        time.sleep(1.0)
        return build_topic_briefs_offline(topic, group_items)

    monkeypatch.setattr(
        "app.services.news_summarizer._summarize_topic_with_flash",
        blocked_summary,
    )
    start = time.monotonic()
    briefs = summarize_all_topics(items, settings)
    elapsed = time.monotonic() - start

    assert elapsed < 0.3, f"摘要总超时应快速降级，实际 {elapsed:.2f}s"
    assert {brief.topic for brief in briefs} == {"半导体", "白酒", "商业航天", "人工智能"}
    assert all(brief.provider == "rule-fallback" for brief in briefs)
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_news_summarizer.py::test_summarize_all_topics_total_timeout_falls_back_without_waiting_for_blocked_workers -q
```

Expected: fail because current implementation waits for worker completion.

- [x] **Step 3: Implement minimal code**

Change `summarize_all_topics` to collect within a global deadline and append offline fallbacks for unfinished topics before returning.

- [x] **Step 4: Verify green**

Run:

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_news_summarizer.py tests/test_news_service_prefetch.py -q
```

Expected: both timeout suites pass.

### Task 3: Frontend Discovery Stream Lifecycle

**Files:**
- Create: `apps/web/src/components/FundDiscoveryPanel.stream-lifecycle.test.tsx`
- Modify: `apps/web/src/components/FundDiscoveryPanel.tsx`

- [x] **Step 1: Write the failing test**

Render `FundDiscoveryPanel` with an active `streamingDiscovery` and an abort spy in `discoveryStreamAbortRef`, unmount the component, and assert the abort spy is not called.

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
cd apps/web && npm.cmd test -- --run src/components/FundDiscoveryPanel.stream-lifecycle.test.tsx
```

Expected: fail because current `FundDiscoveryPanel` unmount cleanup aborts the stream.

- [x] **Step 3: Implement minimal code**

Remove the `useEffect` cleanup that calls `discoveryStreamAbortRef.current?.abort()`. Keep `handleCancelStream` as the explicit cancel path.

- [x] **Step 4: Verify green**

Run:

```bash
cd apps/web && npm.cmd test -- --run src/components/FundDiscoveryPanel.stream-lifecycle.test.tsx src/lib/discoveryStreamApi.test.ts
cd apps/web && npm.cmd run typecheck
```

Expected: dashboard stream lifecycle test and TypeScript pass.

### Task 4: Integrated Verification

**Files:**
- Verify changed backend and frontend files.

- [x] **Step 1: Run focused backend suite**

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_news_service_prefetch.py tests/test_news_summarizer.py tests/test_discovery_streaming.py -q
```

- [x] **Step 2: Run focused frontend suite**

```bash
cd apps/web && npm test -- --run src/components/Dashboard.discovery-stream.test.tsx src/lib/discoveryStreamApi.test.ts
cd apps/web && npx tsc --noEmit
```

- [x] **Step 3: Run smoke timing commands when environment allows**

```bash
cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_analysis.py --mode fast --stream --label after-fix
cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_discovery.py --mode fast --label after-fix
```

Expected: stage timing prints show news/summary no longer waits past configured budgets; if external native dependencies fail, report exact error and rely on deterministic regression tests.
