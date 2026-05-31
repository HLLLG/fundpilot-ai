# Remove Automation Workflow & Unify Async Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete inbox watcher / scheduler automation entirely, unify "generate report" to always use async job flow, and replace the existing banner with a right-bottom floating task panel.

**Architecture:** Backend drops all inbox/scheduler endpoints and services; lifespan.py only initializes job_store's ThreadPoolExecutor. Frontend removes AutomationPanel/DailyWorkflowBar/polling, adds a new `JobStatusFloat` component that owns polling internally and calls back to Dashboard on completion.

**Tech Stack:** FastAPI/Python (backend), Next.js/React/TypeScript/Tailwind (frontend), SQLite via existing job_store.

---

## File Map

| Action | Path |
|--------|------|
| DELETE | `apps/api/app/services/inbox_watcher.py` |
| DELETE | `apps/api/app/services/inbox_processor.py` |
| DELETE | `apps/api/app/services/scheduler.py` |
| DELETE | `apps/api/app/services/inbox_store.py` |
| DELETE | `apps/api/tests/test_automation.py` |
| DELETE | `apps/web/src/components/AutomationPanel.tsx` |
| DELETE | `apps/web/src/components/DailyWorkflowBar.tsx` |
| MODIFY | `apps/api/app/lifespan.py` |
| MODIFY | `apps/api/app/main.py` |
| MODIFY | `apps/api/app/config.py` |
| MODIFY | `.env.example` |
| MODIFY | `apps/web/src/lib/api.ts` |
| MODIFY | `apps/web/src/lib/storage.ts` |
| MODIFY | `apps/web/src/components/Dashboard.tsx` |
| CREATE | `apps/web/src/components/JobStatusFloat.tsx` |

---

## Task 1: Delete backend automation service files

**Files:**
- Delete: `apps/api/app/services/inbox_watcher.py`
- Delete: `apps/api/app/services/inbox_processor.py`
- Delete: `apps/api/app/services/scheduler.py`
- Delete: `apps/api/app/services/inbox_store.py`

- [ ] **Step 1: Delete the four files**

```bash
rm apps/api/app/services/inbox_watcher.py
rm apps/api/app/services/inbox_processor.py
rm apps/api/app/services/scheduler.py
rm apps/api/app/services/inbox_store.py
```

- [ ] **Step 2: Verify files are gone**

```bash
ls apps/api/app/services/
```

Expected: no inbox_watcher.py, inbox_processor.py, scheduler.py, inbox_store.py in the list.

---

## Task 2: Update lifespan.py — remove automation startup

**Files:**
- Modify: `apps/api/app/lifespan.py`

- [ ] **Step 1: Replace lifespan.py with automation-free version**

Write the following content to `apps/api/app/lifespan.py`:

```python
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    yield
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/services/inbox_watcher.py apps/api/app/services/inbox_processor.py apps/api/app/services/scheduler.py apps/api/app/services/inbox_store.py apps/api/app/lifespan.py
git commit -m "chore: delete automation services and strip lifespan startup"
```

---

## Task 3: Update main.py — remove automation endpoints and imports

**Files:**
- Modify: `apps/api/app/main.py`

- [ ] **Step 1: Write the cleaned main.py**

Replace the entire content of `apps/api/app/main.py` with:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import (
    delete_report,
    get_ocr_text_cache,
    get_previous_report,
    get_report,
    list_reports,
    save_ocr_text_cache,
)
from app.lifespan import app_lifespan
from app.models import AnalysisRequest, FundProfile, InvestorProfile
from app.services.analyze_pipeline import run_analysis
from app.services.fund_profile import FundProfileService, parse_profile_from_text
from app.services.job_store import create_analysis_job, get_job_response
from app.services.ocr_engine import OcrEngine
from app.services.ocr_parser import parse_holdings_from_text
from app.services.report_diff import diff_reports
from app.services.report_export import report_to_markdown


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/ocr")
async def parse_ocr(
    raw_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    text = raw_text or ""
    upload_path: Path | None = None
    cache_hit = False

    if file is not None and file.filename:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = settings.upload_dir / Path(file.filename).name
        file_bytes = await file.read()
        upload_path.write_bytes(file_bytes)
        cache_key = hashlib.sha256(file_bytes).hexdigest()
        if not text:
            cached_text = get_ocr_text_cache(cache_key)
            if cached_text is not None:
                text = cached_text
                cache_hit = True
            else:
                try:
                    text = OcrEngine().extract_text(upload_path)
                    save_ocr_text_cache(cache_key, text)
                except Exception as exc:
                    return {
                        "raw_text": "",
                        "upload_path": str(upload_path),
                        "holdings": [],
                        "error": f"OCR 识别失败：{exc}",
                    }

    holdings = FundProfileService().resolve_holdings(parse_holdings_from_text(text))
    return {
        "raw_text": text,
        "upload_path": str(upload_path) if upload_path else None,
        "holdings": [holding.model_dump() for holding in holdings],
        "cache_hit": cache_hit,
    }


@app.post("/api/analyze")
def analyze(request: AnalysisRequest) -> dict:
    try:
        report = run_analysis(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report.model_dump(mode="json")


@app.post("/api/analyze/async")
def analyze_async(request: AnalysisRequest) -> dict:
    if not request.holdings:
        raise HTTPException(status_code=400, detail="至少需要一条基金持仓")
    job_id = create_analysis_job(request)
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job_response(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@app.get("/api/reports")
def reports() -> list[dict]:
    return list_reports()


@app.get("/api/reports/{report_id}")
def report_detail(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@app.get("/api/reports/{report_id}/diff")
def report_diff(report_id: str) -> dict:
    current = get_report(report_id)
    if current is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    previous = get_previous_report(report_id)
    if previous is None:
        return {"has_previous": False, "diff": None}
    return {
        "has_previous": True,
        "diff": diff_reports(current, previous),
    }


@app.get("/api/reports/{report_id}/markdown")
def report_markdown(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"markdown": report_to_markdown(report)}


@app.delete("/api/reports/{report_id}")
def remove_report(report_id: str) -> dict:
    if not delete_report(report_id):
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"ok": True, "id": report_id}


@app.post("/api/fund-profiles/ocr")
async def parse_fund_profile(
    raw_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    text = raw_text or ""
    upload_path: Path | None = None

    if file is not None and file.filename:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = settings.upload_dir / Path(file.filename).name
        upload_path.write_bytes(await file.read())
        if not text:
            try:
                text = OcrEngine().extract_text(upload_path)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"基金详情 OCR 失败：{exc}") from exc

    profile = parse_profile_from_text(text)
    if profile is None:
        raise HTTPException(status_code=422, detail="未能从截图中识别基金代码和档案字段")

    FundProfileService().save_profile(profile)
    payload = profile.model_dump(mode="json")
    payload["raw_text"] = text
    payload["upload_path"] = str(upload_path) if upload_path else None
    return payload


@app.get("/api/fund-profiles")
def fund_profiles() -> list[dict]:
    return [
        profile.model_dump(mode="json")
        for profile in FundProfileService().list_profiles()
    ]


@app.get("/api/fund-profiles/export")
def export_fund_profiles() -> dict:
    profiles = FundProfileService().list_profiles()
    return {
        "version": 1,
        "count": len(profiles),
        "profiles": [profile.model_dump(mode="json") for profile in profiles],
    }


@app.post("/api/fund-profiles/import")
def import_fund_profiles(payload: dict) -> dict:
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise HTTPException(status_code=400, detail="profiles 必须是数组")

    service = FundProfileService()
    saved = 0
    for item in raw_profiles:
        profile = FundProfile.model_validate(item)
        service.save_profile(profile)
        saved += 1
    return {"ok": True, "saved": saved}
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/main.py
git commit -m "chore: remove automation endpoints from main.py"
```

---

## Task 4: Clean config.py and .env.example

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write cleaned config.py**

Replace `apps/api/app/config.py` with:

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]

# DeepSeek V4 系列 API 文档：单次输出上限 384K tokens
DEEPSEEK_MAX_OUTPUT_TOKENS = 384_000


class Settings(BaseSettings):
    app_name: str = "Fund AI Assistant"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    db_path: Path = PROJECT_ROOT / "data" / "app.db"
    upload_dir: Path = PROJECT_ROOT / "uploads"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_timeout_seconds: float = 300
    deepseek_max_tokens: int = DEEPSEEK_MAX_OUTPUT_TOKENS
    deepseek_max_tokens_report: int = DEEPSEEK_MAX_OUTPUT_TOKENS
    news_enabled: bool = True
    news_max_topics: int = 5
    news_per_topic: int = 5
    news_tool_max_rounds: int = 3

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="FUND_AI_",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def refresh_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
```

- [ ] **Step 2: Write cleaned .env.example**

Replace `.env.example` with:

```
# Backend
FUND_AI_DEEPSEEK_API_KEY=sk-your-deepseek-key
FUND_AI_DEEPSEEK_BASE_URL=https://api.deepseek.com
FUND_AI_DEEPSEEK_MODEL=deepseek-v4-pro
FUND_AI_DEEPSEEK_TIMEOUT_SECONDS=300
FUND_AI_DEEPSEEK_MAX_TOKENS=384000
FUND_AI_DEEPSEEK_MAX_TOKENS_REPORT=384000
FUND_AI_NEWS_ENABLED=true
FUND_AI_NEWS_MAX_TOPICS=5
FUND_AI_NEWS_PER_TOPIC=5
FUND_AI_NEWS_TOOL_MAX_ROUNDS=3
FUND_AI_DB_PATH=D:\Code\HL_Project\fundpilot-ai\data\app.db
FUND_AI_UPLOAD_DIR=D:\Code\HL_Project\fundpilot-ai\uploads
FUND_AI_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# Frontend
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/config.py .env.example
git commit -m "chore: remove automation config variables"
```

---

## Task 5: Delete automation test file, run backend tests

**Files:**
- Delete: `apps/api/tests/test_automation.py`

- [ ] **Step 1: Delete test_automation.py**

```bash
rm apps/api/tests/test_automation.py
```

- [ ] **Step 2: Run all backend tests**

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -v
```

Expected: All remaining tests pass (previously ~38 tests; after deleting test_automation.py, 3 fewer = ~35 tests, all green). No import errors about inbox_store, inbox_processor, or scheduler.

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/test_automation.py
git commit -m "chore: delete automation tests"
```

---

## Task 6: Clean api.ts — remove automation-related types and functions

**Files:**
- Modify: `apps/web/src/lib/api.ts`

- [ ] **Step 1: Write cleaned api.ts**

Replace the entire content of `apps/web/src/lib/api.ts` with:

```typescript
export type Holding = {
  fund_code: string;
  fund_name: string;
  holding_amount: number;
  return_percent: number;
  daily_profit?: number | null;
  daily_return_percent?: number | null;
  holding_profit?: number | null;
  holding_return_percent?: number | null;
  sector_name?: string | null;
  sector_return_percent?: number | null;
  user_note?: string | null;
};

export type InvestorProfile = {
  style: string;
  horizon: string;
  max_drawdown_percent: number;
  concentration_limit_percent: number;
  prefer_dca: boolean;
  avoid_chasing: boolean;
};

export type AnalysisMode = "fast" | "deep";

export type ReportDiff = {
  previous_report_id: string;
  previous_title: string;
  previous_created_at: string;
  risk_level_changed: boolean;
  previous_risk_level: string;
  current_risk_level: string;
  suggested_action_changed: boolean;
  previous_suggested_action: string;
  current_suggested_action: string;
  weighted_return_delta: number;
  holding_changes: Array<{
    type: "added" | "removed" | "changed";
    fund_code?: string;
    fund_name?: string;
    holding_amount?: number;
    return_percent?: number;
    previous_holding_amount?: number;
    previous_return_percent?: number;
    holding_amount_delta?: number;
    return_percent_delta?: number;
  }>;
  recommendation_changes: Array<{
    fund_code: string;
    previous_action?: string | null;
    current_action?: string | null;
  }>;
};

export type ReportDiffResponse = {
  has_previous: boolean;
  diff: ReportDiff | null;
};

export type RiskAlert = {
  code: string;
  severity: "low" | "medium" | "high";
  message: string;
  evidence: string;
};

export type Report = {
  id: string;
  created_at: string;
  title: string;
  risk: {
    level: "low" | "medium" | "high";
    suggested_action: "watch" | "pause_add" | "staggered_add" | "risk_review";
    weighted_return_percent: number;
    alerts: RiskAlert[];
  };
  holdings: Holding[];
  snapshots: Array<{
    fund_code: string;
    fund_name: string;
    latest_nav?: number | null;
    nav_date?: string | null;
    source: string;
    note?: string | null;
  }>;
  market_context: Array<{
    topic: string;
    query: string;
    source: string;
    note: string;
  }>;
  market_news: Array<{
    topic: string;
    title: string;
    published_at?: string | null;
    source?: string | null;
    url?: string | null;
    snippet?: string | null;
    is_today?: boolean;
  }>;
  fund_recommendations: Array<{
    fund_code: string;
    fund_name: string;
    action: string;
    amount_yuan?: number | null;
    amount_note?: string | null;
    news_bullish?: string[];
    news_bearish?: string[];
    points: string[];
  }>;
  summary: string;
  recommendations: string[];
  caveats: string[];
  provider: string;
};

export type FundProfile = {
  fund_code: string;
  fund_name: string;
  aliases: string[];
  holding_amount?: number | null;
  holding_shares?: number | null;
  position_percent?: number | null;
  holding_profit?: number | null;
  holding_return_percent?: number | null;
  holding_cost?: number | null;
  daily_profit?: number | null;
  yesterday_profit?: number | null;
  holding_days?: number | null;
  sector_name?: string | null;
  sector_return_percent?: number | null;
  source: string;
  raw_text?: string;
  upload_path?: string | null;
};

export type OcrResponse = {
  raw_text: string;
  upload_path: string | null;
  holdings: Holding[];
  error?: string;
  cache_hit?: boolean;
};

export type AnalysisJob = {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  error?: string | null;
  created_at: string;
  updated_at: string;
  report?: Report;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function parseOcr(formData: FormData): Promise<OcrResponse> {
  const response = await fetch(`${API_BASE}/api/ocr`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function analysisPayload(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
  analysisMode: AnalysisMode = "deep",
) {
  return {
    holdings,
    profile,
    ocr_text: ocrText,
    analysis_mode: analysisMode,
  };
}

export async function startAnalyzeJob(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
  analysisMode: AnalysisMode = "deep",
): Promise<string> {
  const response = await fetch(`${API_BASE}/api/analyze/async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(analysisPayload(holdings, profile, ocrText, analysisMode)),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.job_id as string;
}

export async function fetchAnalysisJob(jobId: string): Promise<AnalysisJob> {
  const response = await fetch(`${API_BASE}/api/jobs/${jobId}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function waitForAnalysisJob(
  jobId: string,
  options?: { intervalMs?: number; timeoutMs?: number },
): Promise<Report> {
  const intervalMs = options?.intervalMs ?? 1500;
  const timeoutMs = options?.timeoutMs ?? 600_000;
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const job = await fetchAnalysisJob(jobId);
    if (job.status === "completed" && job.report) {
      return job.report;
    }
    if (job.status === "failed") {
      throw new Error(job.error ?? "分析任务失败");
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("分析任务超时，请稍后在历史记录中查看。");
}

export async function listReports(): Promise<Report[]> {
  const response = await fetch(`${API_BASE}/api/reports`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function deleteReport(reportId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
}

export async function fetchReportDiff(reportId: string): Promise<ReportDiffResponse> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/diff`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function fetchReportMarkdown(reportId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/reports/${reportId}/markdown`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const body = await response.json();
  return body.markdown as string;
}

export async function exportFundProfiles(): Promise<{ profiles: FundProfile[] }> {
  const response = await fetch(`${API_BASE}/api/fund-profiles/export`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function importFundProfiles(profiles: FundProfile[]): Promise<{ saved: number }> {
  const response = await fetch(`${API_BASE}/api/fund-profiles/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profiles }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function parseFundProfile(formData: FormData): Promise<FundProfile> {
  const response = await fetch(`${API_BASE}/api/fund-profiles/ocr`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function listFundProfiles(): Promise<FundProfile[]> {
  const response = await fetch(`${API_BASE}/api/fund-profiles`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/lib/api.ts
git commit -m "chore: remove automation types and functions from api.ts"
```

---

## Task 7: Clean storage.ts — remove automation preference keys

**Files:**
- Modify: `apps/web/src/lib/storage.ts`

- [ ] **Step 1: Write cleaned storage.ts**

Replace the entire content of `apps/web/src/lib/storage.ts` with:

```typescript
import type { InvestorProfile } from "@/lib/api";

const PROFILE_KEY = "fundpilot-investor-profile";
const MODE_KEY = "fundpilot-analysis-mode";

export type AnalysisMode = "fast" | "deep";

export function loadInvestorProfile(fallback: InvestorProfile): InvestorProfile {
  if (typeof window === "undefined") {
    return fallback;
  }
  try {
    const raw = window.localStorage.getItem(PROFILE_KEY);
    if (!raw) {
      return fallback;
    }
    return { ...fallback, ...JSON.parse(raw) } as InvestorProfile;
  } catch {
    return fallback;
  }
}

export function saveInvestorProfile(profile: InvestorProfile) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
}

export function loadAnalysisMode(fallback: AnalysisMode = "deep"): AnalysisMode {
  if (typeof window === "undefined") {
    return fallback;
  }
  const raw = window.localStorage.getItem(MODE_KEY);
  return raw === "fast" || raw === "deep" ? raw : fallback;
}

export function saveAnalysisMode(mode: AnalysisMode) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(MODE_KEY, mode);
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/lib/storage.ts
git commit -m "chore: remove automation preference keys from storage.ts"
```

---

## Task 8: Create JobStatusFloat component

**Files:**
- Create: `apps/web/src/components/JobStatusFloat.tsx`

- [ ] **Step 1: Create JobStatusFloat.tsx**

Write the following content to `apps/web/src/components/JobStatusFloat.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { CheckCircle, Loader2, XCircle } from "lucide-react";
import type { Report } from "@/lib/api";
import { waitForAnalysisJob } from "@/lib/api";

type JobState = "running" | "completed" | "failed";

interface JobStatusFloatProps {
  jobId: string | null;
  onComplete: (report: Report) => void;
  onClose: () => void;
  onRetry: () => void;
}

export function JobStatusFloat({ jobId, onComplete, onClose, onRetry }: JobStatusFloatProps) {
  const [state, setState] = useState<JobState>("running");
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    setState("running");
    setError(null);
    setReport(null);

    let cancelled = false;
    waitForAnalysisJob(jobId)
      .then((result) => {
        if (cancelled) return;
        setReport(result);
        setState("completed");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "分析失败，请重试。");
        setState("failed");
      });

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (!jobId) {
    return null;
  }

  return (
    <div className="fixed bottom-6 right-6 z-50 w-72 rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_32px_rgba(0,0,0,0.12)]">
      {state === "running" && (
        <div className="flex items-start gap-3">
          <Loader2 size={20} className="mt-0.5 shrink-0 animate-spin text-blue-600" />
          <div>
            <div className="text-sm font-bold text-slate-900">正在生成报告…</div>
            <div className="mt-0.5 text-xs text-slate-500">预计 10–30 秒，可继续操作页面</div>
          </div>
        </div>
      )}

      {state === "completed" && (
        <div>
          <div className="flex items-start gap-3">
            <CheckCircle size={20} className="mt-0.5 shrink-0 text-emerald-500" />
            <div className="text-sm font-bold text-slate-900">报告已生成</div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (report) onComplete(report);
              }}
              className="flex-1 rounded-xl bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-700"
            >
              查看报告
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
      )}

      {state === "failed" && (
        <div>
          <div className="flex items-start gap-3">
            <XCircle size={20} className="mt-0.5 shrink-0 text-red-500" />
            <div>
              <div className="text-sm font-bold text-slate-900">分析失败</div>
              {error && (
                <div className="mt-0.5 line-clamp-2 text-xs text-slate-500">{error}</div>
              )}
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={onRetry}
              className="flex-1 rounded-xl bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-700"
            >
              重试
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/JobStatusFloat.tsx
git commit -m "feat: add JobStatusFloat component for async analysis progress"
```

---

## Task 9: Delete frontend automation component files

**Files:**
- Delete: `apps/web/src/components/AutomationPanel.tsx`
- Delete: `apps/web/src/components/DailyWorkflowBar.tsx`

- [ ] **Step 1: Delete the files**

```bash
rm apps/web/src/components/AutomationPanel.tsx
rm apps/web/src/components/DailyWorkflowBar.tsx
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/AutomationPanel.tsx apps/web/src/components/DailyWorkflowBar.tsx
git commit -m "chore: delete AutomationPanel and DailyWorkflowBar components"
```

---

## Task 10: Rewrite Dashboard.tsx — remove automation, wire JobStatusFloat

**Files:**
- Modify: `apps/web/src/components/Dashboard.tsx`

- [ ] **Step 1: Write the new Dashboard.tsx**

Replace the entire content of `apps/web/src/components/Dashboard.tsx` with:

```tsx
"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BadgeCheck,
  BookMarked,
  BrainCircuit,
  Camera,
  FileText,
  History,
  LockKeyhole,
  Table2,
  TrendingUp,
} from "lucide-react";
import type {
  AnalysisMode,
  FundProfile,
  Holding,
  InvestorProfile,
  Report,
} from "@/lib/api";
import {
  exportFundProfiles,
  importFundProfiles,
  listFundProfiles,
  listReports,
  parseFundProfile,
  parseOcr,
  startAnalyzeJob,
} from "@/lib/api";
import { notifyDesktop } from "@/lib/notifications";
import {
  loadAnalysisMode,
  loadInvestorProfile,
  saveAnalysisMode,
  saveInvestorProfile,
} from "@/lib/storage";
import { FundProfilePanel } from "@/components/FundProfilePanel";
import { HistoryRail } from "@/components/HistoryRail";
import { HoldingTable } from "@/components/HoldingTable";
import { JobStatusFloat } from "@/components/JobStatusFloat";
import { ReportPanel } from "@/components/ReportPanel";
import { RiskControls } from "@/components/RiskControls";
import { StatusPill } from "@/components/StatusPill";
import { UploadDropzone } from "@/components/UploadDropzone";

const sampleText = `华夏中证电网设备主题ETF发起式联接A
015608
持有金额 5,280.66
持有收益率 -3.25%

天弘中证红利低波动100A
008114
持有金额 3,500
持有收益率 1.45%`;

const defaultProfile: InvestorProfile = {
  style: "稳健",
  horizon: "半年到一年",
  max_drawdown_percent: 8,
  concentration_limit_percent: 35,
  prefer_dca: true,
  avoid_chasing: true,
};

type TabId = "capture" | "profiles" | "analysis" | "history";

const tabs: Array<{
  id: TabId;
  label: string;
  description: string;
  icon: React.ReactNode;
}> = [
  {
    id: "capture",
    label: "截图识别",
    description: "识别总览并校对持仓",
    icon: <Camera size={17} />,
  },
  {
    id: "profiles",
    label: "基金档案",
    description: "一次建档，后续自动匹配",
    icon: <BookMarked size={17} />,
  },
  {
    id: "analysis",
    label: "分析报告",
    description: "生成并查看每日操作建议",
    icon: <FileText size={17} />,
  },
  {
    id: "history",
    label: "历史日报",
    description: "回看已保存报告",
    icon: <History size={17} />,
  },
];

export function Dashboard() {
  const [file, setFile] = useState<File | null>(null);
  const [rawText, setRawText] = useState("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [profile, setProfile] = useState<InvestorProfile>(defaultProfile);
  const [report, setReport] = useState<Report | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [profiles, setProfiles] = useState<FundProfile[]>([]);
  const [detailText, setDetailText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [isParsing, setIsParsing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isProfiling, setIsProfiling] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>("capture");
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("deep");
  const [profileReady, setProfileReady] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const totalAmount = useMemo(
    () => holdings.reduce((sum, holding) => sum + Number(holding.holding_amount || 0), 0),
    [holdings],
  );

  const loadHistory = async () => {
    try {
      setReports(await listReports());
    } catch {
      setReports([]);
    }
  };

  const loadProfiles = async () => {
    try {
      setProfiles(await listFundProfiles());
    } catch {
      setProfiles([]);
    }
  };

  useEffect(() => {
    setProfile(loadInvestorProfile(defaultProfile));
    setAnalysisMode(loadAnalysisMode("deep"));
    setProfileReady(true);
    void loadHistory();
    void loadProfiles();
  }, []);

  useEffect(() => {
    if (!profileReady) return;
    saveInvestorProfile(profile);
  }, [profile, profileReady]);

  useEffect(() => {
    if (!profileReady) return;
    saveAnalysisMode(analysisMode);
  }, [analysisMode, profileReady]);

  const handleParse = async (fileOverride?: File) => {
    setIsParsing(true);
    setMessage(null);
    try {
      const formData = new FormData();
      const fileToUpload = fileOverride ?? file;
      if (fileToUpload) {
        formData.append("file", fileToUpload);
      }
      if (rawText.trim()) {
        formData.append("raw_text", rawText);
      }
      const result = await parseOcr(formData);
      setRawText(result.raw_text);
      setHoldings(result.holdings);
      setMessage(
        result.error ??
          (result.holdings.length ? "识别完成，请在下方校对持仓。" : "未识别到基金代码，可以手动新增持仓。"),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "OCR 识别失败，请改用手动文本。");
    } finally {
      setIsParsing(false);
    }
  };

  const handleFileSelect = (selectedFile: File) => {
    setFile(selectedFile);
    void handleParse(selectedFile);
  };

  const runAnalyze = async (targetHoldings: Holding[]) => {
    if (!targetHoldings.length) {
      setMessage("请先上传截图或录入至少一条持仓。");
      return;
    }
    setIsSubmitting(true);
    setMessage(null);
    try {
      const jobId = await startAnalyzeJob(targetHoldings, profile, rawText, analysisMode);
      setActiveJobId(jobId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交分析任务失败。");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAnalyze = async () => {
    await runAnalyze(holdings);
  };

  const handleJobComplete = async (completedReport: Report) => {
    setReport(completedReport);
    await loadHistory();
    setActiveTab("analysis");
    setActiveJobId(null);
    notifyDesktop("FundPilot 日报已生成", { body: completedReport.title });
    setMessage(
      analysisMode === "fast"
        ? "快速模式日报已生成（Flash + 预取新闻）。"
        : "日报已生成并保存到历史记录。",
    );
  };

  const handleJobClose = () => {
    setActiveJobId(null);
  };

  const handleJobRetry = async () => {
    setActiveJobId(null);
    await runAnalyze(holdings);
  };

  const handleRunDaily = async () => {
    setMessage(null);
    try {
      let nextHoldings = holdings;
      if (!nextHoldings.length) {
        if (!file && !rawText.trim()) {
          setMessage("请先上传养基宝总览截图，或粘贴 OCR 文本。");
          return;
        }
        setIsParsing(true);
        const formData = new FormData();
        if (file) formData.append("file", file);
        if (rawText.trim()) formData.append("raw_text", rawText);
        const result = await parseOcr(formData);
        setRawText(result.raw_text);
        setHoldings(result.holdings);
        nextHoldings = result.holdings;
        setIsParsing(false);
      }
      if (!nextHoldings.length) {
        return;
      }
      await runAnalyze(nextHoldings);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "今日一键分析失败。");
      setIsParsing(false);
      setIsSubmitting(false);
    }
  };

  const handleExportProfiles = async () => {
    try {
      const payload = await exportFundProfiles();
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `fund-profiles-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage(`已导出 ${payload.profiles.length} 条基金档案。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导出档案失败。");
    }
  };

  const handleImportProfiles = async (selectedFile: File) => {
    try {
      const text = await selectedFile.text();
      const payload = JSON.parse(text) as { profiles?: FundProfile[] };
      const profilesToImport = payload.profiles ?? (Array.isArray(payload) ? payload : []);
      if (!Array.isArray(profilesToImport) || profilesToImport.length === 0) {
        throw new Error("JSON 中未找到 profiles 数组。");
      }
      const result = await importFundProfiles(profilesToImport);
      await loadProfiles();
      setMessage(`已导入 ${result.saved} 条基金档案。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入档案失败。");
    }
  };

  const handleProfileForm = async (formData: FormData) => {
    setIsProfiling(true);
    setMessage(null);
    try {
      const profileResult = await parseFundProfile(formData);
      setDetailText(profileResult.raw_text ?? "");
      await loadProfiles();
      setActiveTab("profiles");
      setMessage(`基金档案已保存：${profileResult.fund_name}（${profileResult.fund_code}）`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "基金详情建档失败。");
    } finally {
      setIsProfiling(false);
    }
  };

  const handleProfileFile = (selectedFile: File) => {
    const formData = new FormData();
    formData.append("file", selectedFile);
    void handleProfileForm(formData);
  };

  const handleProfileText = () => {
    const formData = new FormData();
    formData.append("raw_text", detailText);
    void handleProfileForm(formData);
  };

  return (
    <main className="premium-bg min-h-screen">
      <div className="mx-auto flex min-h-screen w-full max-w-[1480px] flex-col px-4 py-5 sm:px-6 lg:px-8">
        <nav className="mb-5 flex items-center justify-between gap-4 rounded-full border border-white/70 bg-white px-4 py-3 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-blue-600 text-white shadow-[0_12px_28px_rgba(23,119,255,0.28)]">
              <BrainCircuit size={22} />
            </div>
            <div>
              <div className="text-sm font-black text-slate-950">FundPilot AI</div>
              <div className="text-xs text-slate-500">私人基金投研助手</div>
            </div>
          </div>
          <div className="hidden items-center gap-2 md:flex">
            <StatusPill tone="blue">本地优先</StatusPill>
            <StatusPill tone="green">DeepSeek V4 Pro</StatusPill>
            <StatusPill tone="amber">人工确认</StatusPill>
          </div>
        </nav>

        <header className="mb-5 grid gap-5 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
          <div>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <StatusPill tone="dark">MVP 工作台</StatusPill>
              <StatusPill tone="blue">截图到日报</StatusPill>
            </div>
            <h1 className="max-w-4xl text-3xl font-black leading-tight text-slate-950 sm:text-4xl">
              把支付宝基金截图，变成一份可追溯的每日操作日报。
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
              先识别持仓，再套你的稳健风控线，最后让模型做研究员。它不会替你下单，只帮你把"该不该动"讲清楚。
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
            <MetricCard icon={<TrendingUp size={18} />} label="持仓总额" value={`¥${totalAmount.toLocaleString("zh-CN")}`} />
            <MetricCard icon={<LockKeyhole size={18} />} label="风险底线" value={`${profile.max_drawdown_percent}%`} />
            <MetricCard icon={<BadgeCheck size={18} />} label="日报数量" value={`${reports.length}`} />
          </div>
        </header>

        {message ? (
          <div className="mb-4 flex items-center justify-between gap-3 rounded-3xl border border-blue-100 bg-white px-5 py-4 text-sm font-semibold text-slate-700 shadow-sm">
            <span>{message}</span>
            <ArrowRight className="text-blue-600" size={18} />
          </div>
        ) : null}

        <TabNav activeTab={activeTab} onSelect={setActiveTab} />

        <div className="min-w-0 flex-1">
          {activeTab === "capture" ? (
            <div className="grid min-w-0 gap-6">
              <div className="grid min-w-0 gap-6 lg:grid-cols-[0.9fr_1.1fr]">
                <UploadDropzone
                  rawText={rawText}
                  isBusy={isParsing}
                  selectedFileName={file?.name ?? null}
                  onRawTextChange={setRawText}
                  onFileSelect={handleFileSelect}
                  onParse={handleParse}
                  onLoadSample={() => setRawText(sampleText)}
                />
                <RiskControls
                  profile={profile}
                  analysisMode={analysisMode}
                  onAnalysisModeChange={setAnalysisMode}
                  onChange={setProfile}
                  onAnalyze={() => void handleAnalyze()}
                  isBusy={isSubmitting}
                />
              </div>
              {holdings.length > 0 || rawText ? (
                <div className="min-w-0">
                  <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
                    <Table2 size={18} className="text-blue-600" />
                    持仓校对
                  </div>
                  <HoldingTable holdings={holdings} onChange={setHoldings} />
                </div>
              ) : null}
            </div>
          ) : null}

          {activeTab === "profiles" ? (
            <FundProfilePanel
              profiles={profiles}
              detailText={detailText}
              isBusy={isProfiling}
              onDetailTextChange={setDetailText}
              onFileSelect={handleProfileFile}
              onParseText={handleProfileText}
              onRefresh={loadProfiles}
              onExport={() => void handleExportProfiles()}
              onImport={(selectedFile) => void handleImportProfiles(selectedFile)}
            />
          ) : null}

          {activeTab === "analysis" ? (
            <div className="flex min-w-0 flex-col gap-6">
              <RiskControls
                profile={profile}
                analysisMode={analysisMode}
                onAnalysisModeChange={setAnalysisMode}
                onChange={setProfile}
                onAnalyze={() => void handleAnalyze()}
                isBusy={isSubmitting}
              />
              <ReportPanel report={report} />
            </div>
          ) : null}

          {activeTab === "history" ? (
            <HistoryRail
              reports={reports}
              onRefresh={loadHistory}
              onSelect={(selectedReport) => {
                setReport(selectedReport);
                setActiveTab("analysis");
              }}
              onDeleted={(reportId) => {
                if (report?.id === reportId) {
                  setReport(null);
                }
              }}
            />
          ) : null}
        </div>
      </div>

      <JobStatusFloat
        jobId={activeJobId}
        onComplete={(completedReport) => void handleJobComplete(completedReport)}
        onClose={handleJobClose}
        onRetry={() => void handleJobRetry()}
      />
    </main>
  );
}

function TabNav({
  activeTab,
  onSelect,
}: {
  activeTab: TabId;
  onSelect: (tab: TabId) => void;
}) {
  return (
    <div className="glass-panel mb-5 overflow-x-auto rounded-[24px] p-2">
      <div className="grid min-w-[640px] grid-cols-4 gap-2">
        {tabs.map((tab) => {
          const active = tab.id === activeTab;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSelect(tab.id)}
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-3 rounded-[18px] px-4 py-3 text-left transition ${
                active
                  ? "bg-blue-600 text-white shadow-[0_14px_32px_rgba(23,119,255,0.24)]"
                  : "bg-white text-slate-600 hover:bg-blue-50 hover:text-blue-700"
              }`}
            >
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl ${
                  active ? "bg-white/15 text-white" : "bg-blue-50 text-blue-600"
                }`}
              >
                {tab.icon}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-black">{tab.label}</span>
                <span className={`mt-0.5 block truncate text-xs ${active ? "text-slate-300" : "text-slate-400"}`}>
                  {tab.description}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="glass-panel rounded-[24px] px-5 py-4">
      <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
        <span className="text-blue-600">{icon}</span>
        {label}
      </div>
      <div className="mt-2 text-2xl font-black text-slate-950">{value}</div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/Dashboard.tsx
git commit -m "feat: rewrite Dashboard — remove automation, wire JobStatusFloat"
```

---

## Task 11: Run frontend type-check and build

**Files:** none new

- [ ] **Step 1: Run typecheck**

```bash
cd apps/web && npm run typecheck
```

Expected: No TypeScript errors. If errors appear about missing imports (AutomationPanel, DailyWorkflowBar, storage functions), fix the specific import in the relevant file.

- [ ] **Step 2: Run lint**

```bash
cd apps/web && npm run lint
```

Expected: No lint errors.

- [ ] **Step 3: Run build**

```bash
cd apps/web && npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 4: Commit if any auto-fixes were needed**

```bash
git add -p
git commit -m "fix: resolve typecheck/lint issues after automation removal"
```

(Only commit if there were actual fixes; skip if nothing changed.)

---

## Task 12: Final verification — run all backend tests

- [ ] **Step 1: Run pytest**

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -v
```

Expected: All tests pass. The count should be roughly 35 (was ~38, minus the 3 deleted automation tests). No import errors.

- [ ] **Step 2: Final commit**

```bash
git add .
git commit -m "chore: update PROJECT_CONTEXT.md after automation removal"
```

Wait — before committing, update `docs/PROJECT_CONTEXT.md` to remove references to inbox/scheduler from the capability list, HTTP API table, and environment variables section. Then commit.

Specifically in `docs/PROJECT_CONTEXT.md`:
- Remove from capability list: `自动化 | uploads/inbox 监视 OCR、/api/analyze/async 后台任务、工作日 14:25 提醒`  → keep only: `异步分析 | /api/analyze/async 后台任务，悬浮面板查看进度`
- Remove from the `前端偏好` row: `自动分析、异步分析、已读收件箱事件`
- Remove the 收件箱 and 定时提醒 sections from 核心业务流
- Remove inbox/scheduler rows from HTTP API table
- Remove the 自动化（阶段 2）environment variables section
- Remove inbox_watcher.py, inbox_processor.py, scheduler.py, inbox_store.py, AutomationPanel.tsx, DailyWorkflowBar.tsx from the directory listing
- Add JobStatusFloat.tsx to the frontend components listing
