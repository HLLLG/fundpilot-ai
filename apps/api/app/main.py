from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.database import (
    delete_report,
    get_fund_profile_by_code,
    get_ocr_text_cache,
    get_previous_report,
    get_report,
    list_reports,
    save_ocr_text_cache,
)
from app.lifespan import app_lifespan
from app.database import list_report_chat_messages
from app.models import AllocatePenetrationRequest, AnalysisRequest, FundProfile, ReportChatRequest
from app.services.analyze_pipeline import run_analysis
from app.database import get_portfolio_summary, save_portfolio_summary
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService, parse_profile_from_text
from app.services.holding_validation import (
    build_holding_review,
    enrich_portfolio_summary_source,
    validate_holdings,
)
from app.services.penetration_daily_allocator import allocate_penetration_daily_profit
from app.services.portfolio_parser import parse_portfolio_summary_from_text
from app.services.portfolio_snapshot import (
    build_dashboard_payload,
    get_previous_holdings_for_review,
    save_daily_snapshot,
)
from app.services.job_store import create_analysis_job, get_job_response
from app.services.ocr_engine import OcrEngine
from app.services.ocr_parser import parse_holdings_from_text
from app.services.report_diff import diff_reports
from app.services.report_chat import stream_report_chat
from app.services.report_chat_export import report_chat_to_markdown
from app.services.rebalance_simulator import simulate_rebalance
from app.services.recommendation_outcomes import build_recommendation_outcomes
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
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "deepseek_configured": settings.deepseek_configured,
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

    profile_service = FundProfileService()
    holdings = profile_service.resolve_holdings(parse_holdings_from_text(text))
    previous_holdings = get_previous_holdings_for_review()
    profile_sync = profile_service.sync_profiles_from_holdings(holdings).model_dump()

    portfolio_summary = parse_portfolio_summary_from_text(text)
    if portfolio_summary is not None:
        portfolio_summary = enrich_portfolio_summary_source(portfolio_summary, holdings)
        portfolio_summary = portfolio_summary.model_copy(
            update={"holding_count": len(holdings)}
        )
        save_portfolio_summary(portfolio_summary)

    holding_review = build_holding_review(
        holdings,
        previous_holdings=previous_holdings,
        portfolio_summary=portfolio_summary,
    )

    if holdings:
        save_daily_snapshot(holdings, portfolio_summary)

    return {
        "raw_text": text,
        "upload_path": str(upload_path) if upload_path else None,
        "holdings": [holding.model_dump() for holding in holdings],
        "cache_hit": cache_hit,
        "profile_sync": profile_sync,
        "portfolio_summary": (
            portfolio_summary.model_dump(mode="json") if portfolio_summary else None
        ),
        **holding_review,
    }


@app.post("/api/holdings/allocate-penetration-daily")
def allocate_penetration_daily(request: AllocatePenetrationRequest) -> dict:
    updated = allocate_penetration_daily_profit(
        request.holdings,
        request.account_daily_profit,
    )
    warnings = validate_holdings(
        updated,
        account_daily_profit=request.account_daily_profit,
        account_daily_profit_source=request.account_daily_profit_source,
    )
    row_sum = round(sum(h.daily_profit or 0 for h in updated), 2)
    return {
        "holdings": [holding.model_dump() for holding in updated],
        "holding_warnings": [item.model_dump() for item in warnings],
        "warning_count": len([w for w in warnings if w.severity != "info"]),
        "allocated_total": row_sum,
        "account_daily_profit": round(request.account_daily_profit, 2),
        "method": "sector_weighted",
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


@app.get("/api/reports/{report_id}/outcomes")
def report_outcomes(report_id: str) -> dict:
    current = get_report(report_id)
    if current is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    previous = get_previous_report(report_id)
    return build_recommendation_outcomes(current, previous)


@app.get("/api/reports/{report_id}/rebalance-simulation")
def report_rebalance_simulation(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile

    holdings_raw = report.get("holdings", [])
    if not holdings_raw:
        raise HTTPException(status_code=400, detail="报告中无持仓数据")

    request = AnalysisRequest(
        holdings=[Holding.model_validate(item) for item in holdings_raw],
        profile=InvestorProfile(),
    )
    recs = [
        FundRecommendation.model_validate(item)
        for item in report.get("fund_recommendations", [])
    ]
    return simulate_rebalance(request, recs)


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


@app.get("/api/reports/{report_id}/chat")
def report_chat_history(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"messages": list_report_chat_messages(report_id)}


@app.post("/api/reports/{report_id}/chat")
def report_chat(report_id: str, body: ReportChatRequest) -> StreamingResponse:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    def event_stream():
        try:
            for payload in stream_report_chat(
                report_id,
                body.message.strip(),
                chat_mode=body.chat_mode,
            ):
                yield f"data: {payload}\n\n"
        except ValueError as exc:
            yield f"data: {{\"type\":\"error\",\"message\":{json.dumps(str(exc), ensure_ascii=False)}}}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/reports/{report_id}/chat/markdown")
def report_chat_markdown(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    messages = list_report_chat_messages(report_id)
    return {"markdown": report_chat_to_markdown(report, messages)}


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


@app.get("/api/fund-profiles/{fund_code}/nav-history")
def fund_nav_history(fund_code: str, days: int = 90) -> dict:
    profile = get_fund_profile_by_code(fund_code)
    fund_name = profile.fund_name if profile else ""
    trading_days = max(20, min(days, 365))
    history = FundDataService().get_nav_history(
        fund_code,
        fund_name,
        trading_days=trading_days,
    )
    return history.model_dump(mode="json")


@app.get("/api/portfolio/dashboard")
def portfolio_dashboard() -> dict:
    profiles = FundProfileService().list_profiles()
    summary = get_portfolio_summary()
    payload = build_dashboard_payload(summary=summary, profiles=profiles)
    payload["profiles"] = [profile.model_dump(mode="json") for profile in profiles]
    return payload


@app.get("/api/portfolio/summary")
def portfolio_summary() -> dict:
    profiles = FundProfileService().list_profiles()
    summary = get_portfolio_summary()
    total_from_profiles = sum(
        profile.holding_amount or 0 for profile in profiles if profile.holding_amount
    )
    daily_from_profiles = sum(
        profile.daily_profit or 0
        for profile in profiles
        if profile.daily_profit is not None
    )
    payload = summary.model_dump(mode="json") if summary else {}
    if not payload.get("total_assets") and total_from_profiles:
        payload["total_assets"] = round(total_from_profiles, 2)
    if payload.get("daily_profit") is None and any(
        profile.daily_profit is not None for profile in profiles
    ):
        payload["daily_profit"] = round(daily_from_profiles, 2)
    payload["holding_count"] = len(profiles)
    payload["profiles"] = [profile.model_dump(mode="json") for profile in profiles]
    return payload


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
