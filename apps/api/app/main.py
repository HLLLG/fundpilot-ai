from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.auth.middleware import AuthMiddleware
from app.auth.models import BindWechatRequest, LoginRequest, RegisterRequest, WechatLoginRequest
from app.auth.service import (
    bind_wechat_user,
    get_current_user_public,
    login_user,
    register_user,
    wechat_login_user,
)
from app.config import get_settings
from app.request_context import get_request_user_id
from app.database import (
    delete_portfolio_snapshots_on_or_before,
    delete_report,
    database_file_path,
    delete_discovery_report,
    get_baseline_report_by_days,
    get_discovery_report,
    get_fund_profile_by_code,
    get_investor_profile,
    get_analysis_role_prompt,
    get_discovery_role_prompt,
    get_previous_discovery_report,
    get_previous_report,
    get_report,
    import_database_file,
    list_discovery_chat_messages,
    list_discovery_reports,
    list_reports,
    save_analysis_role_prompt,
    save_discovery_role_prompt,
    save_investor_profile,
)
from app.lifespan import app_lifespan
from app.database import list_report_chat_messages
from app.models import (
    AllocatePenetrationRequest,
    AnalysisPromptSaveRequest,
    AnalysisRequest,
    ApplyHoldingsRequest,
    DiscoveryChatRequest,
    DiscoveryPromptSaveRequest,
    DiscoveryRequest,
    Holding,
    HoldingDetailRequest,
    InvestorProfile,
    RefreshSectorQuotesRequest,
    ReportChatRequest,
    SaveSectorMappingRequest,
    SwingAlertEvaluateRequest,
    UpdateFundProfileRequest,
)
from app.services.analyze_pipeline import run_analysis
from app.database import get_portfolio_summary
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService, migrate_fund_profile_code
from app.services.holding_validation import validate_holdings
from app.services.penetration_daily_allocator import allocate_penetration_daily_profit
from app.services.holding_estimates import sum_daily_profit
from app.services.fund_code_resolver import reconcile_holding_fund_codes, search_funds_by_keyword
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.portfolio_persistence import enrich_loaded_holdings, persist_holdings_after_sector_refresh
from app.services.portfolio_snapshot import build_dashboard_payload
from app.services.job_status_service import resolve_job_status_single_connection
from app.services.job_store import create_analysis_job
from app.services.discovery_job_store import create_discovery_job
from app.services.discovery_chat import stream_discovery_chat
from app.services.discovery_export import discovery_report_to_markdown
from app.services.discovery_diff import diff_discovery_reports
from app.services.discovery_outcomes import (
    build_discovery_outcomes,
    build_discovery_recommendation_accuracy,
)
from app.services.discovery_sector_heat import build_sector_heat_ranking
from app.services.ocr_pipeline import apply_confirmed_holdings, run_ocr_upload_pipeline
from app.services.report_diff import diff_reports
from app.services.report_chat import stream_report_chat
from app.services.report_chat_export import report_chat_to_markdown
from app.services.rebalance_simulator import simulate_rebalance
from app.services.recommendation_accuracy import build_recommendation_accuracy
from app.services.sector_signal_backtest import build_sector_signal_backtest
from app.services.recommendation_outcomes import (
    build_recommendation_outcomes,
    build_weekly_recommendation_outcomes,
)
from app.services.report_export import report_to_markdown
from app.services.sector_quote_diagnostic import run_sector_quote_diagnostic
from app.services.sector_quote_service import apply_sector_mapping_choice, refresh_holdings_sector_quotes
from app.services.sector_intraday_provider import fetch_sector_intraday
from app.services.holding_detail_service import build_holding_detail
from app.services.news_freshness import build_news_pipeline_context
from app.services.news_service import NewsService
from app.services.trading_session import build_trading_session


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=app_lifespan)

app.add_middleware(AuthMiddleware)
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


@app.post("/api/auth/register")
def auth_register(body: RegisterRequest) -> dict:
    try:
        result = register_user(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump()


@app.post("/api/auth/login")
def auth_login(body: LoginRequest) -> dict:
    try:
        result = login_user(body)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return result.model_dump()


@app.get("/api/auth/me")
def auth_me() -> dict:
    try:
        user = get_current_user_public(get_request_user_id())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return user.model_dump()


@app.post("/api/auth/wechat-login")
def auth_wechat_login(body: WechatLoginRequest) -> dict:
    try:
        result = wechat_login_user(body)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return result.model_dump()


@app.post("/api/auth/bind-wechat")
def auth_bind_wechat(body: BindWechatRequest) -> dict:
    try:
        user = bind_wechat_user(get_request_user_id(), body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user.model_dump()


@app.get("/api/reports/recommendation-accuracy")
def recommendation_accuracy(days: int = 30) -> dict:
    limit = max(2, min(days, 50))
    return build_recommendation_accuracy(limit_reports=limit)


@app.get("/api/diagnostics/sector-signal-backtest")
def sector_signal_backtest(
    days: int = 120,
    sectors: str | None = None,
) -> dict:
    """板块短线信号 T→T+1 回测（canonical 板块日线；不传 sectors 时用全部硬编码映射）。"""
    labels = [part.strip() for part in (sectors or "").split(",") if part.strip()]
    return build_sector_signal_backtest(
        labels or None,
        lookback_days=days,
    )


@app.post("/api/news/preview")
def news_preview(body: AnalysisRequest) -> dict:
    """预取要闻并返回时效诊断（不调用 DeepSeek，供生成日报前自检）。"""
    service = NewsService()
    topics = service.topics_from_holdings(body.holdings)
    items = service.prefetch_for_holdings(body.holdings)
    freshness = build_news_pipeline_context(items)
    return {
        "topics": topics,
        "items": [item.model_dump() for item in items],
        "freshness": freshness,
        "trading_session": build_trading_session(),
    }


@app.post("/api/ocr")
async def parse_ocr(
    raw_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    preview: bool = Form(default=False),
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
        preview=preview,
    )


@app.post("/api/portfolio/apply-holdings")
def apply_portfolio_holdings(payload: ApplyHoldingsRequest) -> dict:
    if not payload.holdings:
        raise HTTPException(status_code=400, detail="持仓不能为空")
    return apply_confirmed_holdings(
        payload.holdings,
        detail_profiles=payload.detail_profiles,
    )


@app.get("/api/funds/search")
def search_funds(q: str = "", limit: int = 12) -> dict:
    items = search_funds_by_keyword(q, limit=min(max(limit, 1), 30))
    return {"query": q, "items": items}


@app.get("/api/funds/{fund_code}/primary-sector")
def get_fund_primary_sector_mapping(fund_code: str, fund_name: str | None = None) -> dict:
    from app.services.fund_primary_sector_service import primary_sector_row_for_api

    return primary_sector_row_for_api(fund_code, fund_name=fund_name)


@app.post("/api/funds/{fund_code}/primary-sector/refresh-holdings")
def refresh_fund_primary_sector_from_holdings(fund_code: str, fund_name: str | None = None) -> dict:
    from app.services.fund_primary_sector_service import refresh_primary_sector_for_fund

    return refresh_primary_sector_for_fund(fund_code, fund_name=fund_name)


@app.post("/api/fund-primary-sectors/sync-from-profiles")
def sync_fund_primary_sectors_from_profiles() -> dict:
    from app.services.fund_primary_sector_service import sync_primary_sectors_from_profiles
    from app.services.fund_profile import FundProfileService

    profiles = FundProfileService().list_profiles()
    synced = sync_primary_sectors_from_profiles(profiles)
    return {"ok": True, "synced": synced}


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
    timeout_seconds = None if request.budget == "accurate" else 8.0
    result = refresh_holdings_sector_quotes(
        request.holdings,
        force_refresh=request.force_refresh,
        timeout_seconds=timeout_seconds,
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


@app.get("/api/sector-quotes/diagnostic")
def sector_quotes_diagnostic(timeout_seconds: float = 8.0) -> dict:
    if not get_settings().sector_quotes_enabled:
        raise HTTPException(status_code=503, detail="板块实时行情已关闭")
    return run_sector_quote_diagnostic(timeout_seconds=timeout_seconds)


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
    points, note, session_date, close_change_percent = fetch_sector_intraday(
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
        "close_change_percent": close_change_percent,
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
    return resolve_job_status_single_connection(job_id)


@app.get("/api/fund-discovery/sectors")
def fund_discovery_sectors() -> dict:
    return {"sectors": build_sector_heat_ranking()}


@app.post("/api/fund-discovery/async")
def fund_discovery_async(request: DiscoveryRequest) -> dict:
    if not request.holdings:
        loaded, _, _ = load_persisted_holdings()
        request = request.model_copy(update={"holdings": loaded})
    job_id = create_discovery_job(request)
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/fund-discovery/reports")
def fund_discovery_reports() -> list[dict]:
    return list_discovery_reports()


@app.get("/api/fund-discovery/reports/{report_id}")
def fund_discovery_report_detail(report_id: str) -> dict:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@app.delete("/api/fund-discovery/reports/{report_id}")
def fund_discovery_report_delete(report_id: str) -> dict:
    if not delete_discovery_report(report_id):
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"deleted": True}


@app.get("/api/fund-discovery/reports/{report_id}/diff")
def fund_discovery_report_diff(report_id: str) -> dict:
    current = get_discovery_report(report_id)
    if current is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    previous = get_previous_discovery_report(report_id)
    if previous is None:
        return {"has_previous": False, "message": "暂无上一份推荐报告"}
    return {"has_previous": True, **diff_discovery_reports(current, previous)}


@app.get("/api/fund-discovery/reports/{report_id}/outcomes")
def fund_discovery_report_outcomes(report_id: str, days: int = 7) -> dict:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    window = max(1, min(days, 60))
    return build_discovery_outcomes(report, days=window)


@app.get("/api/fund-discovery/recommendation-accuracy")
def fund_discovery_recommendation_accuracy(days: int = 30) -> dict:
    window = max(7, min(days, 90))
    reports = list_discovery_reports(limit=30)
    return build_discovery_recommendation_accuracy(reports, days=window)


@app.get("/api/fund-discovery/reports/{report_id}/markdown")
def fund_discovery_report_markdown(report_id: str) -> dict:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"markdown": discovery_report_to_markdown(report)}


@app.get("/api/fund-discovery/reports/{report_id}/chat")
def fund_discovery_chat_history(report_id: str) -> dict:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"messages": list_discovery_chat_messages(report_id)}


@app.post("/api/fund-discovery/reports/{report_id}/chat")
def fund_discovery_chat(report_id: str, body: DiscoveryChatRequest) -> StreamingResponse:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    def event_stream():
        try:
            for payload in stream_discovery_chat(
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

    portfolio = (report.get("analysis_facts") or {}).get("portfolio") or {}
    profile = InvestorProfile(
        concentration_limit_percent=float(
            portfolio.get("concentration_limit_percent") or 35
        ),
        max_drawdown_percent=float(portfolio.get("max_drawdown_limit_percent") or 8),
        expected_investment_amount=portfolio.get("expected_investment_amount"),
    )

    request = AnalysisRequest(
        holdings=[Holding.model_validate(item) for item in holdings_raw],
        profile=profile,
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


@app.get("/api/fund-profiles")
def fund_profiles() -> list[dict]:
    return [
        profile.model_dump(mode="json")
        for profile in FundProfileService().list_profiles()
    ]


@app.patch("/api/fund-profiles/{fund_code}")
def patch_fund_profile(fund_code: str, payload: UpdateFundProfileRequest) -> dict:
    profile = get_fund_profile_by_code(fund_code)
    if profile is None:
        raise HTTPException(status_code=404, detail="持仓元数据不存在")

    updates: dict = {}
    if payload.first_purchase_date is not None:
        if payload.first_purchase_date:
            try:
                date.fromisoformat(payload.first_purchase_date)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="首次购入日期格式无效") from exc
        updates["first_purchase_date"] = payload.first_purchase_date

    if payload.fund_name is not None and payload.fund_name.strip():
        updates["fund_name"] = payload.fund_name.strip()

    if payload.fund_code is not None:
        new_code = payload.fund_code.strip().zfill(6)
        if len(new_code) != 6 or not new_code.isdigit():
            raise HTTPException(status_code=400, detail="基金代码格式无效")
        if new_code != fund_code:
            try:
                profile = migrate_fund_profile_code(fund_code, new_code, fund_name=updates.get("fund_name"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            fund_code = new_code

    if updates:
        from app.database import save_fund_profile

        profile = profile.model_copy(update=updates)
        profile = save_fund_profile(profile)

    return profile.model_dump(mode="json")


@app.post("/api/fund-profiles/repair-sectors")
def repair_fund_profile_sectors() -> dict:
    """将历史档案中的无效板块名（如 OCR 误识别为 +）清理并写回数据库。"""
    import json

    from app.database import _connect, save_fund_profile
    from app.models import FundProfile
    from app.services.fund_profile import (
        _sanitize_profile_sector_fields,
        infer_intraday_index_from_fund_name,
        infer_intraday_index_from_sector,
    )

    repaired = 0
    user_id = get_request_user_id()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM fund_profiles WHERE userId = ?",
            (user_id,),
        ).fetchall()
    for row in rows:
        raw = FundProfile.model_validate(json.loads(row["payload"]))
        cleaned = _sanitize_profile_sector_fields(raw)
        inferred_index = infer_intraday_index_from_sector(cleaned.sector_name)
        if not inferred_index:
            inferred_index = infer_intraday_index_from_fund_name(cleaned.fund_name)
        if inferred_index and not cleaned.intraday_index_name:
            cleaned = cleaned.model_copy(update={"intraday_index_name": inferred_index})
            if not cleaned.sector_name:
                from app.services.fund_profile import _infer_related_board_label

                cleaned = cleaned.model_copy(
                    update={"sector_name": _infer_related_board_label(inferred_index)}
                )
        if (
            raw.sector_name != cleaned.sector_name
            or raw.intraday_index_name != cleaned.intraday_index_name
        ):
            save_fund_profile(cleaned)
            repaired += 1
    synced_holdings: list[dict] = []
    if repaired:
        from app.services.portfolio_holdings_service import sync_portfolio_from_profiles

        synced_holdings = [
            h.model_dump() for h in sync_portfolio_from_profiles(refresh_sectors=True)
        ]
    return {"ok": True, "repaired": repaired, "synced_holdings": synced_holdings}


@app.get("/api/fund-profiles/{fund_code}/nav-history")
def fund_nav_history(fund_code: str, days: int = 90) -> dict:
    profile = get_fund_profile_by_code(fund_code)
    fund_name = profile.fund_name if profile else ""
    trading_days = max(20, min(days, 800))
    history = FundDataService().get_nav_history(
        fund_code,
        fund_name,
        trading_days=trading_days,
    )
    return history.model_dump(mode="json")


@app.get("/api/fund-profiles/{fund_code}/nav-history/page")
def fund_nav_history_page(
    fund_code: str,
    limit: int = 30,
    before_date: str | None = None,
    pool_days: int = 800,
) -> dict:
    profile = get_fund_profile_by_code(fund_code)
    fund_name = profile.fund_name if profile else ""
    page_limit = max(10, min(limit, 60))
    pool = max(60, min(pool_days, 800))
    return FundDataService().get_nav_history_page(
        fund_code,
        fund_name,
        limit=page_limit,
        before_date=before_date,
        pool_days=pool,
    )


@app.get("/api/market/index-daily")
def index_daily_history(symbol: str = "000300", days: int = 252) -> dict:
    trading_days = max(20, min(days, 800))
    return FundDataService().get_index_daily_history(
        index_symbol=symbol,
        trading_days=trading_days,
    )


@app.delete("/api/portfolio/snapshots")
def purge_portfolio_snapshots(on_or_before: str) -> dict:
    """清除指定日期（含）及更早的盈亏日快照，保留之后记录。"""
    try:
        datetime.strptime(on_or_before, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="on_or_before 须为 YYYY-MM-DD") from exc
    return delete_portfolio_snapshots_on_or_before(on_or_before)


@app.get("/api/portfolio/dashboard")
def portfolio_dashboard(
    range: str = "today",
    calendar_year: int | None = None,
    calendar_month: int | None = None,
) -> dict:
    profiles = FundProfileService().list_profiles()
    summary = get_portfolio_summary()
    profit_range = range if range in {"today", "week", "month", "year", "all"} else "today"
    payload = build_dashboard_payload(
        summary=summary,
        profiles=profiles,
        profit_range=profit_range,  # type: ignore[arg-type]
        calendar_year=calendar_year,
        calendar_month=calendar_month,
    )
    payload["profiles"] = [profile.model_dump(mode="json") for profile in profiles]
    return payload


@app.get("/api/portfolio/holdings")
def portfolio_holdings() -> dict:
    holdings, source, snapshot_date = load_persisted_holdings()
    holdings = reconcile_holding_fund_codes(holdings)
    holdings = FundProfileService().resolve_holdings(holdings)
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


@app.get("/api/investor-profile")
def investor_profile_get() -> dict:
    profile = get_investor_profile()
    if profile is None:
        profile = InvestorProfile()
    return profile.model_dump()


@app.put("/api/investor-profile")
def investor_profile_put(profile: InvestorProfile) -> dict:
    saved = save_investor_profile(profile)
    return saved.model_dump()


@app.post("/api/swing-alerts/evaluate")
def swing_alerts_evaluate(body: SwingAlertEvaluateRequest) -> dict:
    from app.services.swing_alert_service import evaluate_and_record_swing_alerts

    return evaluate_and_record_swing_alerts(body).model_dump()


@app.get("/api/swing-alerts/today")
def swing_alerts_today(trade_date: str | None = None) -> dict:
    from app.services.swing_alert_service import list_today_swing_alerts

    items = list_today_swing_alerts(trade_date)
    return {"items": [item.model_dump() for item in items]}


@app.get("/api/analysis-prompt")
def analysis_prompt_get() -> dict:
    from app.services.analysis_prompt import build_prompt_config

    return build_prompt_config(get_analysis_role_prompt()).model_dump()


@app.put("/api/analysis-prompt")
def analysis_prompt_put(body: AnalysisPromptSaveRequest) -> dict:
    from app.services.analysis_prompt import build_prompt_config

    saved = save_analysis_role_prompt(body.role_prompt)
    return build_prompt_config(saved).model_dump()


@app.get("/api/discovery-prompt")
def discovery_prompt_get() -> dict:
    from app.services.discovery_prompt import build_prompt_config

    return build_prompt_config(get_discovery_role_prompt()).model_dump()


@app.put("/api/discovery-prompt")
def discovery_prompt_put(body: DiscoveryPromptSaveRequest) -> dict:
    from app.services.discovery_prompt import build_prompt_config

    saved = save_discovery_role_prompt(body.role_prompt)
    return build_prompt_config(saved).model_dump()


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


