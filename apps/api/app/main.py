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
from app.services.inbox_store import get_inbox_event, list_inbox_events, update_inbox_event_status
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


@app.get("/api/automation/status")
def automation_status() -> dict:
    settings = get_settings()
    return {
        "inbox_enabled": settings.inbox_enabled,
        "inbox_dir": str(settings.inbox_dir),
        "inbox_poll_seconds": settings.inbox_poll_seconds,
        "schedule_enabled": settings.schedule_enabled,
        "schedule_time": settings.schedule_time,
        "schedule_weekdays_only": settings.schedule_weekdays_only,
        "schedule_auto_analyze": settings.schedule_auto_analyze,
        "pending_inbox_events": len(list_inbox_events(status="pending", limit=100)),
    }


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


@app.get("/api/inbox/events")
def inbox_events(status: str | None = "pending", limit: int = 20) -> list[dict]:
    if status not in {None, "pending", "consumed", "failed"}:
        raise HTTPException(status_code=400, detail="无效的状态筛选")
    return list_inbox_events(status=status, limit=min(limit, 50))  # type: ignore[arg-type]


@app.post("/api/inbox/events/{event_id}/consume")
def consume_inbox_event(event_id: str) -> dict:
    event = get_inbox_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    updated = update_inbox_event_status(event_id, "consumed")
    return updated or event


@app.post("/api/inbox/events/{event_id}/analyze")
def analyze_inbox_event(
    event_id: str,
    request: AnalysisRequest | None = Body(default=None),
) -> dict:
    event = get_inbox_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    if event["kind"] != "ocr_ready":
        raise HTTPException(status_code=400, detail="仅 OCR 事件可触发分析")

    payload = event["payload"]
    holdings = payload.get("holdings") or []
    if not holdings:
        raise HTTPException(status_code=400, detail="事件中没有可分析的持仓")

    if request is None:
        analysis_request = AnalysisRequest(
            holdings=holdings,
            profile=InvestorProfile(),
            ocr_text=payload.get("raw_text"),
            analysis_mode="fast",
        )
    else:
        analysis_request = request.model_copy(
            update={
                "holdings": request.holdings or holdings,
                "ocr_text": request.ocr_text or payload.get("raw_text"),
            }
        )

    job_id = create_analysis_job(analysis_request)
    update_inbox_event_status(event_id, "consumed")
    return {"job_id": job_id, "status": "pending", "event_id": event_id}


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
