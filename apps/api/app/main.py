from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.config import get_settings
from app.database import (
    delete_report,
    database_file_path,
    get_baseline_report_by_days,
    get_fund_profile_by_code,
    get_ocr_text_cache,
    get_previous_report,
    get_report,
    import_database_file,
    list_reports,
    save_ocr_text_cache,
)
from app.lifespan import app_lifespan
from app.database import list_report_chat_messages
from app.models import (
    AllocatePenetrationRequest,
    AnalysisRequest,
    FundProfile,
    Holding,
    HoldingDetailRequest,
    RefreshSectorQuotesRequest,
    ReportChatRequest,
    SaveSectorMappingRequest,
)
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
from app.services.holding_estimates import sum_daily_profit
from app.services.portfolio_holdings_service import (
    load_persisted_holdings,
    sync_portfolio_from_profiles,
)
from app.services.portfolio_persistence import enrich_loaded_holdings, persist_holdings_after_sector_refresh
from app.services.portfolio_snapshot import (
    build_dashboard_payload,
    get_previous_holdings_for_review,
    save_daily_snapshot,
)
from app.services.job_store import create_analysis_job, get_job_response
from app.services.ocr_engine import OcrEngine
from app.services.ocr_pipeline import run_ocr_upload_pipeline
from app.services.ocr_parser import parse_holdings_from_text
from app.services.report_diff import diff_reports
from app.services.report_chat import stream_report_chat
from app.services.report_chat_export import report_chat_to_markdown
from app.services.rebalance_simulator import simulate_rebalance
from app.services.recommendation_outcomes import (
    build_recommendation_outcomes,
    build_weekly_recommendation_outcomes,
)
from app.services.report_export import report_to_markdown
from app.services.sector_quote_service import apply_sector_mapping_choice, refresh_holdings_sector_quotes
from app.services.sector_intraday_provider import fetch_sector_intraday
from app.services.holding_detail_service import build_holding_detail
from app.services.trading_session import build_trading_session


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


@app.get("/api/trading-session")
def trading_session() -> dict:
    return build_trading_session()


@app.post("/api/ocr")
async def parse_ocr(
    raw_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    file_bytes: bytes | None = None
    filename: str | None = None
    if file is not None and file.filename:
        file_bytes = await file.read()
        filename = file.filename

    return await asyncio.to_thread(
        run_ocr_upload_pipeline,
        text=raw_text or "",
        file_bytes=file_bytes,
        filename=filename,
    )


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


@app.post("/api/holdings/refresh-sector-quotes")
def refresh_sector_quotes(request: RefreshSectorQuotesRequest) -> dict:
    if not get_settings().sector_quotes_enabled:
        raise HTTPException(status_code=503, detail="板块实时行情已关闭")
    result = refresh_holdings_sector_quotes(
        request.holdings,
        force_refresh=request.force_refresh,
    )
    if result.get("ok") and result.get("holdings"):
        refreshed = [Holding.model_validate(item) for item in result["holdings"]]
        fetched_at = None
        if result.get("fetched_at"):
            fetched_at = datetime.fromisoformat(str(result["fetched_at"]))
        enriched = persist_holdings_after_sector_refresh(refreshed, fetched_at=fetched_at)
        result["holdings"] = [holding.model_dump() for holding in enriched]
    return result


@app.post("/api/sector-mappings/apply")
def apply_sector_mapping(request: SaveSectorMappingRequest) -> dict:
    if not get_settings().sector_quotes_enabled:
        raise HTTPException(status_code=503, detail="板块实时行情已关闭")
    try:
        return apply_sector_mapping_choice(
            request.holdings,
            index=request.index,
            source_type=request.source_type,
            source_name=request.source_name,
            source_code=request.source_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sector-quotes/status")
def sector_quotes_status() -> dict:
    settings = get_settings()
    session = build_trading_session()
    auto_allowed = session["session_kind"] in {
        "trading_day_intraday",
        "trading_day_pre_close",
    }
    return {
        "enabled": settings.sector_quotes_enabled,
        "ttl_seconds": settings.sector_quotes_ttl_seconds,
        "auto_interval_seconds": settings.sector_quotes_auto_interval_seconds,
        "auto_refresh_allowed": auto_allowed,
        "session": session,
    }


@app.get("/api/sector-quotes/intraday")
def sector_quotes_intraday(
    source_type: str,
    source_name: str,
    force_refresh: bool = False,
) -> dict:
    if not get_settings().sector_quotes_enabled:
        raise HTTPException(status_code=503, detail="板块实时行情已关闭")
    points, note, session_date = fetch_sector_intraday(
        source_type,
        source_name,
        force_refresh=force_refresh,
    )
    return {
        "source_type": source_type,
        "source_name": source_name,
        "points": points,
        "note": note,
        "session_date": session_date,
    }


@app.post("/api/holdings/detail")
def holding_detail(request: HoldingDetailRequest) -> dict:
    try:
        detail = build_holding_detail(
            request.holdings,
            request.index,
            portfolio_summary=request.portfolio_summary,
            sector_quote_meta=request.sector_quote_meta,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return detail.model_dump(mode="json")


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


@app.get("/api/reports/{report_id}/outcomes-weekly")
def report_outcomes_weekly(report_id: str, days: int = 7) -> dict:
    current = get_report(report_id)
    if current is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    window = max(3, min(days, 30))
    baseline = get_baseline_report_by_days(report_id, days=window)
    return build_weekly_recommendation_outcomes(current, baseline, baseline_days=window)


@app.get("/api/database/export")
def export_database() -> FileResponse:
    db_path = database_file_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="数据库文件不存在")
    return FileResponse(
        path=db_path,
        filename="fundpilot-app.db",
        media_type="application/octet-stream",
    )


@app.post("/api/database/import")
async def import_database(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.endswith(".db"):
        raise HTTPException(status_code=400, detail="请上传 .db 文件")

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    staging = settings.upload_dir / f"import-{file.filename}"
    staging.write_bytes(await file.read())

    try:
        result = import_database_file(staging, backup_current=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"导入失败：{exc}") from exc
    finally:
        if staging.exists():
            staging.unlink()

    return {"ok": True, **result}


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
    synced = sync_portfolio_from_profiles(refresh_sectors=True)
    summary = get_portfolio_summary()
    payload = profile.model_dump(mode="json")
    payload["raw_text"] = text
    payload["upload_path"] = str(upload_path) if upload_path else None
    payload["synced_holdings"] = [holding.model_dump() for holding in synced]
    payload["portfolio_summary"] = summary.model_dump(mode="json") if summary else None
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


@app.get("/api/portfolio/holdings")
def portfolio_holdings() -> dict:
    holdings, source, snapshot_date = load_persisted_holdings()
    holdings = enrich_loaded_holdings(holdings)
    summary = get_portfolio_summary()
    profiles = FundProfileService().list_profiles()
    payload = summary.model_dump(mode="json") if summary else {}
    total_from_holdings = round(sum(holding.holding_amount for holding in holdings), 2)
    if total_from_holdings:
        payload["total_assets"] = total_from_holdings
    if holdings:
        payload["daily_profit"] = sum_daily_profit(holdings)
        if total_from_holdings > (payload["daily_profit"] or 0):
            previous = total_from_holdings - float(payload["daily_profit"])
            if previous > 0:
                payload["daily_return_percent"] = round(
                    float(payload["daily_profit"]) / previous * 100,
                    2,
                )
    payload["holding_count"] = len(holdings)
    return {
        "holdings": [holding.model_dump() for holding in holdings],
        "source": source,
        "snapshot_date": snapshot_date,
        "portfolio_summary": payload or None,
        "profile_count": len(profiles),
    }


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
