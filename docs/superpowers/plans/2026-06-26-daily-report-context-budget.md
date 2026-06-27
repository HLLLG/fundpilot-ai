# Daily Report Context Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce deep daily report context-building latency while preserving factual accuracy through bounded best-effort enhancement data.

**Architecture:** Keep core holdings, NAV, risk, news, and date facts blocking. Move expensive enhancement facts behind explicit time budgets: factor scores, risk metrics, signal backtest, sector fund flow, intraday summary, market flow, and guard policy may degrade to structured unavailable payloads when they exceed budget. The LLM receives explicit availability/confidence fields instead of stale or fabricated facts.

**Tech Stack:** FastAPI service layer, Python `concurrent.futures`, pytest, existing analysis payload/facts services.

---

### Task 1: Add Budget Helper Tests

**Files:**
- Modify: `apps/api/tests/test_analyze_streaming_latency.py`
- Modify: `apps/api/app/services/analysis_payload.py`
- Modify: `apps/api/app/services/analysis_facts.py`

- [ ] **Step 1: Write failing tests**

Add tests that monkeypatch slow enhancement builders and assert `prepare_analysis_bundle(..., budget_enhancements=True)` returns quickly with unavailable metadata rather than blocking.

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_analyze_streaming_latency.py -q
```

Expected: tests fail because budget arguments and fallback metadata do not exist yet.

- [ ] **Step 3: Implement minimal budgeted helpers**

Add small helpers around futures with timeouts. On timeout, return explicit unavailable payloads with `reason="timeout"` and do not use fabricated values.

- [ ] **Step 4: Verify tests pass**

Run the same pytest command and expect all tests in the file to pass.

### Task 2: Apply Budgets In Streaming Daily Report

**Files:**
- Modify: `apps/api/app/services/analyze_streaming.py`
- Modify: `apps/api/app/services/analysis_payload.py`

- [ ] **Step 1: Write failing streaming test**

Add a stream test where context enhancement functions sleep longer than the budget. Assert the stream reaches `generating` and `done` without waiting for all sleepers.

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_analyze_streaming_latency.py::test_stream_analysis_budgeted_context_continues_when_enhancements_are_slow -q
```

Expected: fail before implementation.

- [ ] **Step 3: Pass `budget_enhancements=True` from streaming path**

The synchronous pipeline can keep full behavior. The streaming daily path uses bounded enhancements to preserve responsiveness.

- [ ] **Step 4: Verify test passes**

Run the targeted test and expect pass.

### Task 3: Verify End To End Latency

**Files:**
- Modify: none unless verification reveals a bug.

- [ ] **Step 1: Run backend regression**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_analyze_background_latency.py tests/test_analysis_facts_dates.py tests/test_analyze_streaming.py tests/test_analyze_streaming_latency.py tests/test_analysis_payload_bundle.py tests/test_report_judge_facts_reuse.py tests/test_news_summarizer.py tests/test_news_service_prefetch.py tests/test_analyze_stream_endpoint.py -q
```

- [ ] **Step 2: Run compile check**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m compileall app
```

- [ ] **Step 3: Run real deep stream smoke**

```powershell
cd apps/api
.\.venv\Scripts\python.exe scripts/smoke_run_analysis.py --mode deep --stream --label context-budget
```

Expected: skeleton still appears early, context-building stage is materially shorter than the previous 48-56 seconds, and the report still completes.

