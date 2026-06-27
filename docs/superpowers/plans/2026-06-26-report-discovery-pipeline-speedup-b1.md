# Report Discovery Pipeline Speedup B1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce pre-LLM wait time in report/discovery streaming by overlapping independent data fetches and exposing server-side stage elapsed time.

**Architecture:** Add a request-context-aware thread helper, then use bounded two-worker executors in the streaming pipelines only. Keep data contracts compatible by adding optional `elapsed_ms` to stage events.

**Tech Stack:** Python FastAPI service generators, `ThreadPoolExecutor`, pytest.

---

## File Structure

- Create `apps/api/app/services/pipeline_concurrency.py`: request user context wrapper for worker threads.
- Modify `apps/api/app/services/analyze_streaming.py`: parallelize fund data and news prefetch; attach `elapsed_ms` to stage events.
- Modify `apps/api/app/services/discovery_streaming.py`: parallelize news prefetch and candidate building; attach `elapsed_ms` to stage events.
- Modify `apps/api/tests/test_analyze_streaming.py`: RED/GREEN tests for parallelism and `elapsed_ms`.
- Modify `apps/api/tests/test_discovery_streaming.py`: RED/GREEN tests for parallelism and `elapsed_ms`.

### Task 1: Context-Aware Worker Helper

- [x] **Step 1: Create helper**

Create `pipeline_concurrency.run_with_request_user(user_id, fn)` to set/reset request context around threaded work.

### Task 2: 日报流式并行

- [x] **Step 1: Write failing test**

`test_stream_analysis_prefetches_fund_data_and_news_in_parallel` sleeps 0.35s in fund data and 0.35s in news, expecting total `<0.55s`.

- [x] **Step 2: Verify RED**

Before implementation, elapsed was about `0.70s`.

- [x] **Step 3: Implement**

Use `ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis-prep")` in `stream_analysis`.

- [x] **Step 4: Verify GREEN**

The parallelism test passes.

### Task 3: 荐基流式并行

- [x] **Step 1: Write failing test**

`test_stream_discovery_prefetches_news_while_building_candidates` sleeps 0.35s in candidate pool, 0.35s in enrich, and 0.35s in news, expecting total `<0.9s`.

- [x] **Step 2: Verify RED**

Before implementation, elapsed was about `1.06s`.

- [x] **Step 3: Implement**

Start `NewsService.prefetch_topics` immediately after target sectors are known, while the main thread builds/enriches candidates.

- [x] **Step 4: Verify GREEN**

The discovery parallelism test passes.

### Task 4: Stage Elapsed Metadata

- [x] **Step 1: Write failing tests**

Existing deep streaming tests now assert every stage event has integer `elapsed_ms`.

- [x] **Step 2: Verify RED**

Before implementation, stage events did not include `elapsed_ms`.

- [x] **Step 3: Implement**

Record `started_at = time.monotonic()` and add `elapsed_ms` in `_stage` / `_emit_stage`.

- [x] **Step 4: Verify GREEN**

Both analyze and discovery deep streaming tests pass.

### Task 5: Verification

- [x] **Step 1: Run focused backend suite**

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_analyze_streaming.py tests/test_discovery_streaming.py tests/test_news_service_prefetch.py tests/test_news_summarizer.py -q
```

- [x] **Step 2: Run focused frontend suite**

```bash
cd apps/web && npm.cmd test -- --run src/components/FundDiscoveryPanel.stream-lifecycle.test.tsx src/lib/discoveryStreamApi.test.ts src/lib/streamApi.test.ts
cd apps/web && npm.cmd run typecheck
```

- [ ] **Step 3: Optional smoke**

```bash
cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_analysis.py --mode fast --stream --label b1
cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_discovery.py --mode fast --label b1
```

Use smoke output as timing evidence when local external dependencies are healthy.
