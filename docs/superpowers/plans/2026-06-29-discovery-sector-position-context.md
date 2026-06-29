# Discovery Sector Position Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 20-day position and volume context to discovery sector opportunities, so momentum/setup scoring can avoid stretched sectors and explain better entry hints.

**Architecture:** Introduce a focused `discovery_sector_position` service that summarizes canonical sector daily K-line rows into deterministic context. Feed that context into `discovery_sector_opportunity` as a best-effort, budgeted enhancement; expose a slim subset to the LLM payload.

**Tech Stack:** Python services, pytest, existing canonical sector daily K-line provider.

---

### Task 1: Pure Position Summary

**Files:**
- Create: `apps/api/app/services/discovery_sector_position.py`
- Test: `apps/api/tests/test_discovery_sector_position.py`

- [x] Write tests for `summarize_sector_position()` covering 20-day high/low distance, drawdown, 5d/20d volume ratio, up/down day counts, and position labels.
- [x] Run `cd apps/api && .\.venv\Scripts\python.exe -m pytest tests\test_discovery_sector_position.py -q` and verify import/function failures.
- [x] Implement the pure summarizer with no network dependency.
- [x] Re-run the same test until green.

### Task 2: Budgeted Position Map

**Files:**
- Modify: `apps/api/app/services/discovery_sector_position.py`
- Test: `apps/api/tests/test_discovery_sector_position.py`

- [x] Add test for `build_sector_position_map_for_opportunities()` proving total timeout returns quickly.
- [x] Implement canonical lookup + daily K-line fetch with `max_days=40`, concurrent workers, AkShare fallback for the small position set, and best-effort timeout.
- [x] Re-run `tests\test_discovery_sector_position.py`.

### Task 3: Opportunity Scoring Integration

**Files:**
- Modify: `apps/api/app/services/discovery_sector_opportunity.py`
- Modify: `apps/api/app/services/discovery_streaming.py`
- Modify: `apps/api/app/services/discovery_pipeline.py`
- Test: `apps/api/tests/test_discovery_sector_opportunity.py`

- [x] Add failing tests showing high-extended sectors are penalized and setup sectors with base/early-breakout context get better entry hints.
- [x] Pass `sector_position_by_label` into `select_sector_opportunities()`.
- [x] Load the position map after flow map in both streaming and non-streaming discovery.
- [x] Re-run opportunity and streaming tests.

### Task 4: LLM Payload and Docs

**Files:**
- Modify: `apps/api/app/services/discovery_payload.py`
- Modify: `apps/api/app/services/eastmoney_trends_client.py`
- Modify: `docs/PROJECT_CONTEXT.md`
- Test: `apps/api/tests/test_discovery_payload.py`

- [x] Add payload test requiring slim position fields under `sector_opportunities`.
- [x] Parse `volume` and `amount` from Eastmoney daily K-line rows where available.
- [x] Update project context with the new data fields and usage boundary.
- [x] Run focused discovery tests and one smoke scan.
