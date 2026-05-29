# Fund AI MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a private local fund decision assistant MVP that reads Alipay fund screenshots, lets the user correct holdings, enriches them with market data, and generates DeepSeek V4 Pro analysis reports.

**Architecture:** The MVP uses a Next.js web app for the local dashboard and a FastAPI backend for OCR, fund data, persistence, and DeepSeek analysis. The backend keeps OCR and external APIs behind focused service interfaces so PaddleOCR, AkShare, and model calls can be replaced without changing the UI.

**Tech Stack:** Next.js, React, TypeScript, Tailwind CSS, FastAPI, Pydantic, SQLite, Pytest, DeepSeek OpenAI-compatible API, optional PaddleOCR, optional AkShare.

---

### Task 1: Backend Domain And Tests

**Files:**
- Create: `apps/api/app/models.py`
- Create: `apps/api/app/services/risk.py`
- Create: `apps/api/app/services/ocr_parser.py`
- Create: `apps/api/tests/test_risk.py`
- Create: `apps/api/tests/test_ocr_parser.py`

- [ ] **Step 1: Write failing tests for risk rules and OCR parsing**

Create tests that expect:
- Portfolio drawdown at or below `-8%` raises a high-risk alert.
- A single holding above `35%` of portfolio raises concentration risk.
- OCR text containing fund code, name, holding amount, and return percentage becomes a structured holding draft.

- [ ] **Step 2: Run tests and confirm failures**

Run: `python -m pytest apps/api/tests/test_risk.py apps/api/tests/test_ocr_parser.py -v`
Expected: tests fail because modules do not exist.

- [ ] **Step 3: Implement minimal models, risk engine, and OCR parser**

Implement Pydantic models, deterministic risk rules, and regex-based parsing that can work with PaddleOCR output.

- [ ] **Step 4: Run tests and confirm green**

Run: `python -m pytest apps/api/tests/test_risk.py apps/api/tests/test_ocr_parser.py -v`
Expected: all tests pass.

### Task 2: Backend Services And API

**Files:**
- Create: `apps/api/app/config.py`
- Create: `apps/api/app/database.py`
- Create: `apps/api/app/services/deepseek_client.py`
- Create: `apps/api/app/services/fund_data.py`
- Create: `apps/api/app/services/ocr_engine.py`
- Create: `apps/api/app/main.py`
- Create: `apps/api/tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

Create tests for:
- `GET /health`
- `POST /api/analyze` with manual holdings returns a persisted report.
- Analysis works in offline mode when `DEEPSEEK_API_KEY` is absent.

- [ ] **Step 2: Run API tests and confirm failures**

Run: `python -m pytest apps/api/tests/test_api.py -v`
Expected: tests fail because app routes do not exist.

- [ ] **Step 3: Implement FastAPI routes**

Implement:
- `GET /health`
- `POST /api/ocr`
- `POST /api/analyze`
- `GET /api/reports`
- `GET /api/reports/{report_id}`

- [ ] **Step 4: Run backend tests**

Run: `python -m pytest apps/api/tests -v`
Expected: all backend tests pass.

### Task 3: Frontend Dashboard

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/postcss.config.mjs`
- Create: `apps/web/src/app/layout.tsx`
- Create: `apps/web/src/app/page.tsx`
- Create: `apps/web/src/app/globals.css`
- Create: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/components/*`

- [ ] **Step 1: Add frontend test/lint scripts**

Add TypeScript and ESLint scripts that can validate the app without a browser.

- [ ] **Step 2: Implement the local dashboard**

Implement a distinctive AI-tool-style dashboard inspired by the reference site: strong top bar, premium CTA copy, upload panel, correction table, risk controls, report cards, and history.

- [ ] **Step 3: Run frontend checks**

Run: `npm install` inside `apps/web`, then `npm run lint` and `npm run typecheck`.
Expected: checks pass.

### Task 4: Developer Experience And Verification

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `apps/api/requirements.txt`
- Create: `apps/api/pytest.ini`
- Create: `scripts/dev.ps1`

- [ ] **Step 1: Document setup and environment**

Explain DeepSeek API configuration, optional PaddleOCR setup, local dev commands, and privacy notes.

- [ ] **Step 2: Run full verification**

Run:
- `python -m pytest apps/api/tests -v`
- `npm run lint`
- `npm run typecheck`
- Start API and web dev servers.

- [ ] **Step 3: Browser smoke test**

Open the local web app in a browser, confirm layout renders, upload/manual flow is usable, and the report area is visible without overlap.
