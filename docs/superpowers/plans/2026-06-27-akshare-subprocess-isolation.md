# AkShare Subprocess Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `py_mini_racer` native crashes from taking down the API or smoke process by ensuring service-layer AkShare calls execute only in subprocesses.

**Architecture:** Add a regression test that fails on AST-level `import akshare` / `from akshare` inside `apps/api/app/services`. Existing string scripts that run inside `subprocess.run([sys.executable, "-c", script])` remain valid. Convert remaining direct imports in service modules to subprocess-backed helpers while preserving their current fallbacks and cache behavior.

**Tech Stack:** Python stdlib `ast`, `subprocess`, pytest, existing service modules.

---

### Task 1: Add Isolation Regression Test

**Files:**
- Create: `apps/api/tests/test_akshare_isolation.py`

- [x] **Step 1: Write failing AST test**

The test walks `apps/api/app/services/**/*.py` and fails if any AST import node imports `akshare`.

- [x] **Step 2: Verify RED**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_akshare_isolation.py -q
```

Expected: FAIL listing direct service imports.

### Task 2: Convert Remaining Main-Process Imports

**Files:**
- Modify: `apps/api/app/services/akshare_spot_client.py`
- Modify: `apps/api/app/services/cls_news_client.py`
- Modify: `apps/api/app/services/fund_diagnostics_cache.py`
- Modify: `apps/api/app/services/sector_intraday_provider.py`
- Modify: `apps/api/app/services/us_index_client.py`
- Modify: any additional service file listed by the failing test

- [x] **Step 1: Replace direct imports with subprocess scripts**

Keep each module's return shape unchanged. If a subprocess fails, return the same empty/stale fallback currently used by that module.

Actual RED offenders were `akshare_spot_client.py`, `fund_diagnostics_cache.py`, and `sector_intraday_provider.py`; the other candidate files did not contain AST-level service imports.

- [x] **Step 2: Verify GREEN**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_akshare_isolation.py -q
```

Expected: PASS.

### Task 3: Regression And Smoke

**Files:**
- Modify: none unless regressions reveal a bug.

- [x] **Step 1: Run focused backend tests**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_akshare_isolation.py tests/test_analyze_streaming_latency.py tests/test_news_service_prefetch.py tests/test_analysis_payload_bundle.py -q
```

- [x] **Step 2: Run compile check**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m compileall app
```

- [x] **Step 3: Run deep daily smoke**

```powershell
cd apps/api
.\.venv\Scripts\python.exe scripts\smoke_run_analysis.py --mode deep --stream --label akshare-isolation
```

Expected: no `py_mini_racer` native crash; report reaches `done`.
