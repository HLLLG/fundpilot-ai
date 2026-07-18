from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from app.auth.middleware import AuthMiddleware
from app.auth.models import LoginRequest, RegisterRequest
from app.auth.service import (
    get_current_user_public,
    login_user,
    register_user,
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
    get_most_recent_portfolio_snapshot,
    get_ocr_text_cache,
    get_previous_discovery_report,
    get_previous_report,
    get_report,
    import_database_file,
    list_discovery_chat_messages,
    list_discovery_reports,
    list_portfolio_daily_snapshots,
    list_reports,
    save_analysis_role_prompt,
    save_discovery_role_prompt,
    save_investor_profile,
    save_ocr_text_cache,
)
from app.lifespan import app_lifespan
from app.database import list_report_chat_messages
from app.models import (
    AdjustHoldingRequest,
    AllocatePenetrationRequest,
    AnalysisPromptSaveRequest,
    AnalysisRequest,
    ApplyHoldingsRequest,
    ApplyTransactionsRequest,
    ConfirmPortfolioLedgerBaselineRequest,
    DiscoveryChatRequest,
    DiscoveryPromptSaveRequest,
    DiscoveryRequest,
    Holding,
    HoldingDetailRequest,
    InvestorProfile,
    PortfolioSummary,
    RefreshSectorQuotesRequest,
    ReportChatRequest,
    SaveSectorMappingRequest,
    StreamFollowupRequest,
    SwingAlertEvaluateRequest,
    UpdateFundProfileRequest,
)
from app.services.analyze_pipeline import run_analysis
from app.services.analyze_streaming import stream_analysis
from app.services.decision_data_evidence import resolve_portfolio_preflight
from app.services.decision_quality_snapshot import (
    DecisionQualitySnapshotContractError,
    DecisionQualitySnapshotStorageError,
    read_latest_decision_quality_snapshot,
)
from app.services.async_sse import sse_from_sync_iterator
from app.services.discovery_streaming import stream_discovery
from app.services.stream_session_store import append_stream_followup
from app.database import get_portfolio_summary
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService, migrate_fund_profile_code
from app.services.holding_validation import validate_holdings
from app.services.penetration_daily_allocator import allocate_penetration_daily_profit
from app.services.holding_client import serialize_holdings_for_client
from app.services.fund_code_resolver import reconcile_holding_fund_codes, search_funds_by_keyword
from app.services.portfolio_holdings_service import (
    apply_server_sector_cache_to_holdings,
    build_fast_snapshot_holdings_response,
    build_portfolio_holdings_response,
    load_persisted_holdings,
    remove_holding_from_portfolio,
)
from app.services.official_nav_settlement import settle_official_nav_for_portfolio
from app.services.portfolio_holdings_cache import (
    get_cached_holdings_response,
    get_holdings_cache_generation,
    save_cached_holdings_response,
)
from app.services.portfolio_persistence import persist_holdings_after_sector_refresh
from app.services.portfolio_snapshot import (
    build_dashboard_payload,
    build_factor_scores_payload,
    build_risk_correlation_payload,
    build_risk_metrics_payload,
    clear_factor_facts_cache,
)
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
from app.services.discovery_sector_heat import build_sector_heat_ranking, build_sector_heat_ranking_for_ui
from app.services.board_fund_flow_history import get_board_flow_history
from app.services.theme_board_snapshot import get_theme_board_snapshot
from app.services.us_market_service import get_us_market_snapshot
from app.services.ocr_pipeline import apply_confirmed_holdings, run_ocr_upload_pipeline
from app.services.report_diff import diff_reports
from app.services.report_chat import stream_report_chat
from app.services.retired_market_evidence import sanitize_retired_market_evidence
from app.services.chat_aggregate import aggregate_chat_stream
from app.services.report_chat_export import report_chat_to_markdown
from app.services.rebalance_simulator import simulate_rebalance
from app.services.recommendation_accuracy import build_recommendation_accuracy
from app.services.sector_signal_backtest import build_sector_signal_backtest
from app.services.market_breadth_signal import build_market_breadth_signal
from app.services.fund_return_distribution import build_fund_return_distribution
from app.services.factor_confidence import clear_ic_summary_cache
from app.services.factor_ic_snapshot import (
    FactorIcNewerSnapshotExists,
    FactorIcStorageUnavailable,
    build_factor_ic_status,
    publish_factor_ic_snapshot,
    validate_publish_request,
)
from app.services.factor_live_calibration import (
    FactorLiveCalibrationStorageUnavailable,
    build_factor_live_calibration_status,
)
from app.services.factor_ic_universe_snapshot import (
    FactorIcUniverseConflict,
    FactorIcUniverseStorageUnavailable,
    publish_factor_ic_universe_snapshot,
    read_factor_ic_universe_history,
    validate_factor_ic_universe_publish_request,
)
from app.services.shadow_escalation_digest import build_shadow_escalation_digest
from app.services.decision_score_shadow import build_decision_score_shadow_digest
from app.services.evidence_maturity import build_evidence_maturity_status
from app.services.recommendation_outcomes import (
    build_recommendation_outcomes,
    build_weekly_recommendation_outcomes,
)
from app.services.report_export import report_to_markdown
from app.services.sector_quote_diagnostic import run_sector_quote_diagnostic
from app.services.sector_quote_service import apply_sector_mapping_choice, refresh_holdings_sector_quotes
from app.services.sector_intraday_provider import fetch_sector_intraday
from app.services.holding_detail_service import build_holding_detail
from app.services.holding_detail_cache import (
    get_cached_holding_detail,
    holding_detail_fingerprint,
    save_cached_holding_detail,
)
from app.services.holding_intraday_warmup import schedule_warm_holdings_intraday
from app.services.news_freshness import build_news_pipeline_context
from app.services.news_service import NewsService
from app.services.portfolio_mutation_guard import PortfolioMutationLockError
from app.services.trading_session import build_trading_session


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=app_lifespan)
logger = logging.getLogger(__name__)
HOLDINGS_READ_TIMEOUT_SECONDS = 25.0

app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.resolved_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PortfolioMutationLockError)
async def portfolio_mutation_lock_error_handler(
    request: Request,
    _exc: PortfolioMutationLockError,
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "持仓正在同步，请稍后重试"},
        headers={"Retry-After": "2", **_cors_error_response_headers(request)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        raise exc
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
        headers=_cors_error_response_headers(request),
    )


def _cors_error_response_headers(request: Request) -> dict[str, str]:
    """Keep allowed origins visible when ServerErrorMiddleware builds a 500.

    Starlette's outer ServerErrorMiddleware catches exceptions after they have
    crossed the configured CORSMiddleware, so its generated response otherwise
    lacks CORS headers and browsers misreport the underlying 500 as a CORS
    failure. Never reflect an origin unless it matches the configured policy.
    """

    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        return {}
    allowed = origin in settings.cors_origin_list
    pattern = settings.resolved_cors_origin_regex
    if not allowed and pattern:
        try:
            allowed = re.fullmatch(pattern, origin) is not None
        except re.error:
            allowed = False
    if not allowed:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


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


def _require_factor_ic_publish_token(
    supplied: Annotated[
        str | None,
        Header(alias="X-Factor-IC-Publish-Token"),
    ] = None,
) -> None:
    expected = (get_settings().factor_ic_publish_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="因子 IC 发布未配置")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="因子 IC 发布凭证无效")


def _require_decision_quality_read_token(
    supplied: Annotated[
        str | None,
        Header(alias="X-Decision-Quality-Read-Token"),
    ] = None,
) -> None:
    """Authorize the isolated read-only D2 operations surface."""

    no_store = {"Cache-Control": "private, no-store, max-age=0"}
    expected = (get_settings().decision_quality_read_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="决策质量快照只读接口未配置",
            headers=no_store,
        )
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="决策质量快照只读凭证无效",
            headers=no_store,
        )


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if normalized == etag:
            return True
    return False


@app.get(
    "/api/internal/decision-quality/evaluations/latest",
    include_in_schema=False,
)
def get_latest_decision_quality_evaluation(
    user_id: str | None = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    _authorized: None = Depends(_require_decision_quality_read_token),
) -> Response:
    """Return one precomputed, redacted snapshot without running evaluation."""

    response_headers = {
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if user_id is None or not user_id.strip().isdigit() or int(user_id) <= 0:
        raise HTTPException(
            status_code=422,
            detail="user_id 必须为正整数",
            headers=response_headers,
        )
    normalized_user_id = int(user_id)
    try:
        payload = read_latest_decision_quality_snapshot(user_id=normalized_user_id)
    except (
        DecisionQualitySnapshotContractError,
        DecisionQualitySnapshotStorageError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail="决策质量快照暂不可用",
            headers=response_headers,
        ) from exc
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="尚无预计算的决策质量快照",
            headers=response_headers,
        )
    content_hash = str(payload.get("content_hash") or "").strip().lower()
    if len(content_hash) != 64 or any(
        character not in "0123456789abcdef" for character in content_hash
    ):
        raise HTTPException(
            status_code=503,
            detail="决策质量快照暂不可用",
            headers=response_headers,
        )
    etag = f'"{content_hash}"'
    response_headers["ETag"] = etag
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=response_headers)
    return JSONResponse(content=payload, headers=response_headers)


@app.post("/api/internal/factor-ic-snapshots", include_in_schema=False)
def publish_factor_ic(
    body: dict,
    _authorized: None = Depends(_require_factor_ic_publish_token),
) -> dict:
    try:
        request = validate_publish_request(body)
        result = publish_factor_ic_snapshot(request)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcNewerSnapshotExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FactorIcStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    clear_ic_summary_cache()
    clear_factor_facts_cache()
    return result


@app.post("/api/internal/factor-ic-universe-snapshots", include_in_schema=False)
def publish_factor_ic_universe(
    body: dict,
    _authorized: None = Depends(_require_factor_ic_publish_token),
) -> dict:
    try:
        request = validate_factor_ic_universe_publish_request(body)
        return publish_factor_ic_universe_snapshot(request)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcUniverseConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FactorIcUniverseStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/internal/factor-ic-universe-snapshots", include_in_schema=False)
def get_factor_ic_universe_history(
    start_date: date | None = None,
    end_date: date | None = None,
    days: int = 365,
    max_snapshots: int = 60,
    stride_days: int = 7,
    include_members: bool = True,
    _authorized: None = Depends(_require_factor_ic_publish_token),
) -> dict:
    try:
        return read_factor_ic_universe_history(
            start_date=start_date,
            end_date=end_date,
            days=days,
            max_snapshots=max_snapshots,
            stride_days=stride_days,
            include_members=include_members,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcUniverseStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/diagnostics/factor-ic-status")
def factor_ic_status() -> dict:
    return build_factor_ic_status()


@app.get("/api/diagnostics/factor-live-calibration")
def factor_live_calibration() -> dict:
    """当前用户的只读量化影子校准；达到门槛也只进入人工复核。"""
    try:
        return build_factor_live_calibration_status(user_id=get_request_user_id())
    except FactorLiveCalibrationStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/diagnostics/decision-score-shadow")
def decision_score_shadow_digest(limit: int = 30) -> dict:
    """当前用户最近荐基报告中的 DecisionScore v1 影子覆盖与差异摘要。"""

    bounded_limit = max(1, min(limit, 100))
    return build_decision_score_shadow_digest(
        list_discovery_reports(limit=bounded_limit)
    )


@app.get("/api/diagnostics/evidence-maturity")
def evidence_maturity_status() -> Response:
    """当前用户的采集健康与证据成熟度；只读且绝不触发即时评估。"""

    payload = build_evidence_maturity_status(user_id=get_request_user_id())
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/api/reports/recommendation-accuracy")
def recommendation_accuracy(days: int = 30) -> dict:
    from app.services.decision_outcome_persistence import (
        OutcomeEvidenceConflict,
        OutcomeEvidencePersistenceError,
    )

    limit = max(2, min(days, 50))
    try:
        return build_recommendation_accuracy(
            limit_reports=limit,
            persist_outcomes=True,
        )
    except OutcomeEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutcomeEvidencePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


@app.get("/api/diagnostics/market-breadth")
def market_breadth() -> dict:
    """大盘情绪温度计（M1.1）：全用户共享（与全市场相关、非按用户区分），供市场 Tab 与
    生成日报诊断区的 `MarketBreadthGauge` 复用同一份数据，不因认证中间件拦截而额外区分用户。"""
    return build_market_breadth_signal()


@app.get("/api/diagnostics/fund-return-distribution")
def fund_return_distribution() -> dict:
    """最近已公布净值日的开放式基金份额涨跌分布。

    只使用官方净值，不把盘中估值冒充正式收益；A/C/E 等份额代码分别计数。
    """
    return build_fund_return_distribution()


@app.get("/api/diagnostics/shadow-escalation-digest")
def shadow_escalation_digest(days: int = 7) -> dict:
    """M6.3：灰度复盘摘要（近 N 天双向 guard 升级判定触发情况，按当前登录用户的历史
    日报/荐基报告聚合）。路径沿用 `/api/diagnostics/*` 既有命名（设计文档原文建议
    `/api/admin/shadow-digest`，改为与同批新增的 `/api/diagnostics/market-breadth`、
    已有的 `/api/diagnostics/sector-signal-backtest` 一致的前缀，避免引入 `/api/admin/`
    这个本项目此前完全没有使用过的新前缀）。"""
    lookback = max(1, min(days, 30))
    return build_shadow_escalation_digest(lookback_days=lookback)


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


@app.post("/api/transactions/ocr")
async def parse_transactions_ocr(
    raw_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    """支付宝「交易记录」截图 → ParsedTransaction[]（不写库）。"""
    file_bytes: bytes | None = None
    filename: str | None = None
    if file is not None and file.filename:
        file_bytes = await file.read()
        filename = file.filename

    return await asyncio.to_thread(
        _build_transactions_ocr_response,
        raw_text or "",
        file_bytes,
        filename,
    )


def _build_transactions_ocr_response(
    raw_text: str,
    file_bytes: bytes | None,
    filename: str | None,
) -> dict:
    from app.services.alipay_transactions_parser import parse_alipay_transactions
    from app.services.fund_code_resolver import lookup_fund_code_by_name
    from app.services.ocr_engine import OcrEngine
    from app.services.ocr_parser import detect_ocr_source
    from app.services.trading_session import resolve_confirm_date

    text = raw_text
    if not text and file_bytes and filename:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = settings.upload_dir / Path(filename).name
        upload_path.write_bytes(file_bytes)
        cache_key = hashlib.sha256(file_bytes).hexdigest()
        cached_text = get_ocr_text_cache(cache_key)
        if cached_text is not None:
            text = cached_text
        else:
            try:
                text = OcrEngine().extract_text(upload_path)
                save_ocr_text_cache(cache_key, text)
            except Exception as exc:  # noqa: BLE001
                return {"transactions": [], "ocr_source": "unknown", "error": f"OCR 识别失败：{exc}"}

    transactions = parse_alipay_transactions(text) if text else []
    enriched: list[dict] = []
    for parsed in transactions:
        if not parsed.confirm_date:
            parsed = parsed.model_copy(update={"confirm_date": resolve_confirm_date(parsed.trade_time)})
        if not parsed.fund_code:
            code, _ = lookup_fund_code_by_name(parsed.fund_name)
            if code:
                parsed = parsed.model_copy(update={"fund_code": code})
        enriched.append(parsed.model_dump(mode="json"))

    return {
        "transactions": enriched,
        "ocr_source": detect_ocr_source(text) if text else "unknown",
    }


@app.post("/api/transactions/apply")
def apply_transactions(payload: ApplyTransactionsRequest) -> dict:
    from app.services.portfolio_ledger_service import PositionTruthStoreUnavailable
    from app.services.transaction_ledger import (
        TransactionTruthConflict,
        apply_parsed_transactions,
    )

    try:
        return apply_parsed_transactions(payload.transactions)
    except TransactionTruthConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "conflicts": exc.conflicts},
        ) from exc
    except PositionTruthStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/portfolio/ledger-baseline")
def portfolio_ledger_baseline_status() -> dict:
    from app.services.portfolio_ledger_service import (
        get_portfolio_ledger_baseline_status,
    )

    return get_portfolio_ledger_baseline_status()


@app.put("/api/portfolio/ledger-baseline")
def confirm_ledger_baseline(
    payload: ConfirmPortfolioLedgerBaselineRequest,
) -> dict:
    from app.services.portfolio_ledger_service import (
        PositionTruthStoreUnavailable,
        confirm_portfolio_ledger_baseline,
    )

    try:
        result = confirm_portfolio_ledger_baseline(payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PositionTruthStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # Portfolio read cache is intentionally not reused after an authoritative
    # baseline change; the Web client also hydrates immediately after success.
    from app.services.portfolio_holdings_cache import bump_holdings_cache_generation

    bump_holdings_cache_generation()
    return result


@app.get("/api/funds/{fund_code}/transactions")
def fund_transactions(fund_code: str) -> dict:
    from app.database import list_fund_transactions

    return {
        "transactions": [
            tx.model_dump(mode="json") for tx in list_fund_transactions(fund_code=fund_code)
        ]
    }


@app.post("/api/portfolio/apply-holdings")
def apply_portfolio_holdings(payload: ApplyHoldingsRequest) -> dict:
    if not payload.holdings:
        raise HTTPException(status_code=400, detail="持仓不能为空")
    response = apply_confirmed_holdings(
        payload.holdings,
    )
    save_cached_holdings_response(response)
    return response


@app.delete("/api/portfolio/holdings/{fund_code}")
def delete_portfolio_holding(fund_code: str, fund_name: str | None = None) -> dict:
    from app.services.portfolio_ledger_service import (
        PositionCloseConflict,
        PositionTruthStoreUnavailable,
    )

    try:
        payload = remove_holding_from_portfolio(fund_code, fund_name=fund_name)
    except PositionCloseConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "transaction_ids": exc.transaction_ids},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PositionTruthStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    save_cached_holdings_response(payload)
    return payload


@app.patch("/api/portfolio/holdings/{fund_code}/adjust")
def adjust_portfolio_holding(fund_code: str, payload: AdjustHoldingRequest) -> dict:
    from app.services.holding_adjust_service import (
        ConfirmedSharesAmountConflict,
        adjust_holding_in_portfolio,
    )

    try:
        response = adjust_holding_in_portfolio(fund_code, payload)
    except ConfirmedSharesAmountConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_cached_holdings_response(response)
    return response


@app.post("/api/portfolio/settle-official-nav")
def settle_portfolio_official_nav() -> dict:
    payload = settle_official_nav_for_portfolio()
    if payload.get("ok") and not payload.get("skipped") and payload.get("holdings"):
        save_cached_holdings_response(payload)
    return payload


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
        "holdings": serialize_holdings_for_client(updated),
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
    # Client holdings are a display snapshot, not portfolio membership truth.
    # A stale tab must never be able to delete a fund by refreshing quotes.
    current_holdings, current_source, snapshot_date, _ = load_persisted_holdings(
        fetch_benchmark=False,
    )
    refresh_holdings = current_holdings or request.holdings
    result = refresh_holdings_sector_quotes(
        refresh_holdings,
        force_refresh=request.force_refresh,
        timeout_seconds=timeout_seconds,
    )
    if result.get("ok") and result.get("holdings"):
        refreshed = [Holding.model_validate(item) for item in result["holdings"]]
        fetched_at = None
        if result.get("fetched_at"):
            fetched_at = datetime.fromisoformat(str(result["fetched_at"]))
        enriched = persist_holdings_after_sector_refresh(
            refreshed,
            fetched_at=fetched_at,
            with_official_nav=request.budget == "accurate",
        )
        result["holdings"] = serialize_holdings_for_client(enriched)
        cache_payload = build_portfolio_holdings_response(
            enriched,
            source=current_source if current_holdings else "snapshot",
            snapshot_date=snapshot_date,
            refreshed_at=fetched_at,
            fetch_benchmark=False,
        )
        save_cached_holdings_response(cache_payload)
        user_id = get_request_user_id()
        schedule_warm_holdings_intraday(
            enriched,
            user_key=str(user_id),
            user_id=user_id,
        )
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
        holding = request.holdings[request.index]
        fingerprint = holding_detail_fingerprint(
            fund_code=holding.fund_code,
            holding_amount=holding.holding_amount,
        )
        cached = get_cached_holding_detail(holding.fund_code, fingerprint)
        if cached is not None:
            return cached

        detail = build_holding_detail(
            request.holdings,
            request.index,
            portfolio_summary=request.portfolio_summary,
            sector_quote_meta=request.sector_quote_meta,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=400, detail="持仓索引超出范围") from exc

    payload = detail.model_dump(mode="json")
    save_cached_holding_detail(holding.fund_code, fingerprint, payload)
    return payload


@app.post("/api/analyze")
def analyze(request: AnalysisRequest) -> dict:
    request = request.model_copy(update={"analysis_mode": "deep"})
    try:
        report = run_analysis(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report.model_dump(mode="json")


@app.post("/api/analyze/async")
def analyze_async(request: AnalysisRequest) -> dict:
    request = request.model_copy(update={"analysis_mode": "deep"})
    try:
        preflight = resolve_portfolio_preflight(
            request.holdings,
            allow_stale=request.allow_stale_portfolio_snapshot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not preflight.holdings:
        raise HTTPException(status_code=400, detail="至少需要一条基金持仓")
    # Keep the original client holdings in the queued payload. Only the retired
    # main-generation mode is normalized above. The worker performs the
    # authoritative preflight again and must still be able to audit a client
    # versus server mismatch instead of comparing the server snapshot to itself.
    job_id = create_analysis_job(request)
    return {"job_id": job_id, "status": "pending"}


@app.post("/api/analyze/stream")
async def analyze_stream_endpoint(request: AnalysisRequest) -> StreamingResponse:
    request = request.model_copy(update={"analysis_mode": "deep"})
    user_id = get_request_user_id()

    async def event_stream():
        async for chunk in sse_from_sync_iterator(stream_analysis(request, user_id=user_id)):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/analyze/stream/{session_id}/followup")
def analyze_stream_followup(session_id: str, body: StreamFollowupRequest) -> dict:
    ok, message, status_code = append_stream_followup(session_id, body.message)
    if not ok:
        raise HTTPException(status_code=status_code, detail=message)
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    return resolve_job_status_single_connection(job_id)


@app.get("/api/fund-discovery/sectors")
def fund_discovery_sectors() -> dict:
    return {"sectors": build_sector_heat_ranking_for_ui()}


@app.get("/api/market/sector-labels")
def market_sector_labels() -> dict:
    from app.services.sector_registry import list_theme_board_labels

    return {"labels": list_theme_board_labels()}


@app.get("/api/market/theme-boards")
def market_theme_boards(
    sort: str = "change",
    force_refresh: bool = False,
) -> dict:
    if sort not in {"change", "inflow"}:
        raise HTTPException(status_code=400, detail="sort 须为 change 或 inflow")
    holdings: list = []
    if get_request_user_id() is not None:
        loaded, _, _, _ = load_persisted_holdings(fetch_benchmark=False)
        holdings = loaded
    return get_theme_board_snapshot(
        force_refresh=force_refresh,
        holdings=holdings,
        sort=sort,  # type: ignore[arg-type]
    )


@app.get("/api/market/board-flow-history")
def market_board_flow_history(
    sector_label: str | None = None,
    board_code: str | None = None,
    range: str = "week",
    force_refresh: bool = False,
) -> dict:
    if range not in {"week", "month"}:
        raise HTTPException(status_code=400, detail="range 须为 week 或 month")
    if not sector_label and not board_code:
        raise HTTPException(status_code=400, detail="须提供 sector_label 或 board_code")
    return get_board_flow_history(
        sector_label=sector_label,
        board_code=board_code,
        flow_range=range,  # type: ignore[arg-type]
        force_refresh=force_refresh,
    )


@app.get("/api/market/us-overview")
def market_us_overview(force_refresh: bool = False) -> dict:
    """美股概览：纳指/标普/道指期货 + USD/CNY + QDII 盘前参考涨跌。

    任何数据源失败均返回 200，通过各 ``*_status`` / ``available`` / ``stale`` /
    ``message`` 表达降级（需求 7），绝不抛 5xx、绝不编造数值。``force_refresh``
    跳过服务端缓存重新聚合（需求 4.5）。与其它 ``/api/market/*`` 接口一致，无需 JWT。
    """
    snapshot = get_us_market_snapshot(force_refresh=force_refresh)
    return snapshot.model_dump(mode="json")


@app.post("/api/fund-discovery/async")
def fund_discovery_async(request: DiscoveryRequest) -> dict:
    request = request.model_copy(update={"analysis_mode": "deep"})
    if not request.holdings:
        loaded, _, _, _ = load_persisted_holdings()
        request = request.model_copy(update={"holdings": loaded})
    job_id = create_discovery_job(request)
    return {"job_id": job_id, "status": "pending"}


@app.post("/api/fund-discovery/stream")
async def fund_discovery_stream_endpoint(request: DiscoveryRequest) -> StreamingResponse:
    request = request.model_copy(update={"analysis_mode": "deep"})
    if not request.holdings:
        loaded, _, _, _ = await asyncio.to_thread(load_persisted_holdings)
        request = request.model_copy(update={"holdings": loaded})
    user_id = get_request_user_id()

    async def event_stream():
        async for chunk in sse_from_sync_iterator(stream_discovery(request, user_id=user_id)):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/fund-discovery/reports")
def fund_discovery_reports() -> list[dict]:
    return [
        sanitize_retired_market_evidence(report)
        for report in list_discovery_reports()
    ]


@app.get("/api/fund-discovery/reports/{report_id}")
def fund_discovery_report_detail(report_id: str) -> dict:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return sanitize_retired_market_evidence(report)


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
    return {
        "has_previous": True,
        **diff_discovery_reports(
            sanitize_retired_market_evidence(current),
            sanitize_retired_market_evidence(previous),
        ),
    }


@app.get("/api/fund-discovery/reports/{report_id}/outcomes")
def fund_discovery_report_outcomes(report_id: str, days: int = 7) -> dict:
    from app.services.decision_outcome_persistence import (
        OutcomeEvidenceConflict,
        OutcomeEvidencePersistenceError,
        persist_discovery_outcome_result,
    )

    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    window = max(1, min(days, 60))
    result = build_discovery_outcomes(report, days=window)
    try:
        return persist_discovery_outcome_result(report, result)
    except OutcomeEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutcomeEvidencePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/fund-discovery/recommendation-accuracy")
def fund_discovery_recommendation_accuracy(days: int = 30) -> dict:
    from app.services.decision_outcome_persistence import (
        OutcomeEvidenceConflict,
        OutcomeEvidencePersistenceError,
    )

    # 兼容旧 7/30 日调用，同时开放新版 T+5/T+20/T+60 研究窗口。
    window = max(1, min(days, 90))
    reports = list_discovery_reports(limit=30)
    try:
        return build_discovery_recommendation_accuracy(
            reports,
            days=window,
            persist_outcomes=True,
        )
    except OutcomeEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutcomeEvidencePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/fund-discovery/reports/{report_id}/markdown")
def fund_discovery_report_markdown(report_id: str) -> dict:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "markdown": discovery_report_to_markdown(
            sanitize_retired_market_evidence(report)
        )
    }


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


@app.post("/api/fund-discovery/reports/{report_id}/chat/sync")
def fund_discovery_chat_sync(report_id: str, body: DiscoveryChatRequest) -> dict:
    report = get_discovery_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    try:
        result = aggregate_chat_stream(
            stream_discovery_chat(
                report_id,
                body.message.strip(),
                chat_mode=body.chat_mode,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response: dict = {
        "user_message": result.user_message,
        "message": result.message,
        "chat_mode": result.chat_mode,
    }
    if result.model is not None:
        response["model"] = result.model
    return response


@app.get("/api/reports")
def reports() -> list[dict]:
    return [sanitize_retired_market_evidence(report) for report in list_reports()]


@app.get("/api/reports/{report_id}")
def report_detail(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return sanitize_retired_market_evidence(report)


@app.get("/api/reports/{report_id}/outcomes")
def report_outcomes(report_id: str) -> dict:
    from app.services.decision_outcome_persistence import (
        OutcomeEvidenceConflict,
        OutcomeEvidencePersistenceError,
        persist_daily_outcome_result,
    )

    current = get_report(report_id)
    if current is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    previous = get_previous_report(report_id)
    result = build_recommendation_outcomes(current, previous)
    try:
        return persist_daily_outcome_result(current, result)
    except OutcomeEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutcomeEvidencePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/reports/{report_id}/outcomes-weekly")
def report_outcomes_weekly(report_id: str, days: int = 7) -> dict:
    from app.services.decision_outcome_persistence import (
        OutcomeEvidenceConflict,
        OutcomeEvidencePersistenceError,
        persist_daily_outcome_result,
    )

    current = get_report(report_id)
    if current is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    window = max(3, min(days, 30))
    baseline = get_baseline_report_by_days(report_id, days=window)
    result = build_weekly_recommendation_outcomes(
        current,
        baseline,
        baseline_days=window,
    )
    try:
        return persist_daily_outcome_result(current, result)
    except OutcomeEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutcomeEvidencePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        "diff": diff_reports(
            sanitize_retired_market_evidence(current),
            sanitize_retired_market_evidence(previous),
        ),
    }


@app.get("/api/reports/{report_id}/markdown")
def report_markdown(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "markdown": report_to_markdown(sanitize_retired_market_evidence(report))
    }


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


@app.post("/api/reports/{report_id}/chat/sync")
def report_chat_sync(report_id: str, body: ReportChatRequest) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    try:
        aggregated = aggregate_chat_stream(
            stream_report_chat(
                report_id,
                body.message.strip(),
                chat_mode=body.chat_mode,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    response: dict = {
        "user_message": aggregated.user_message,
        "message": aggregated.message,
        "chat_mode": aggregated.chat_mode,
    }
    if aggregated.model is not None:
        response["model"] = aggregated.model
    return response


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


@app.get("/api/portfolio/risk-metrics")
def portfolio_risk_metrics() -> dict:
    """组合风险体检（懒加载）：日快照 + 沪深300 对齐后计算 Beta/Alpha 等。"""
    history_rows = list_portfolio_daily_snapshots(limit=400, include_holdings=False)
    latest = get_most_recent_portfolio_snapshot()
    holdings_models = (
        [Holding.model_validate(item) for item in latest.get("holdings", [])]
        if latest and latest.get("holdings")
        else []
    )
    return build_risk_metrics_payload(history_rows, holdings_models)


@app.get("/api/portfolio/risk-correlation")
def portfolio_risk_correlation(lookback_days: int = 120) -> dict:
    """持仓相关性矩阵（懒加载，逐只拉净值历史）。"""
    lookback = max(30, min(lookback_days, 400))
    holdings, *_ = load_persisted_holdings()
    return build_risk_correlation_payload(holdings, lookback_days=lookback)


@app.get("/api/portfolio/factor-scores")
def portfolio_factor_scores() -> dict:
    """持仓因子体检（懒加载）：优先使用当前 IC 快照的分类型研究模型。

    v2/v3 快照按基金类型同类比较；没有新版研究模型时兼容旧排行榜 300 只横截面。
    """
    holdings, *_ = load_persisted_holdings()
    ic_context: dict = {}
    try:
        from app.services.factor_confidence import load_ic_context

        ic_context = load_ic_context()
    except Exception:  # noqa: BLE001 — IC 上下文缺失时保留旧横截面兼容路径
        ic_context = {}

    status = ic_context.get("status") or {}
    research_model = (
        ic_context.get("research_model")
        if ic_context.get("state") == "available"
        and status.get("stale") is not True
        and isinstance(ic_context.get("research_model"), dict)
        else None
    )
    payload = build_factor_scores_payload(
        holdings,
        research_model=research_model,
    )

    try:
        from app.services.factor_confidence import factor_reliability

        if research_model is not None:
            for fund in payload.get("funds") or []:
                segment = str(fund.get("peer_group") or "")
                fund["factor_reliability"] = (
                    factor_reliability(
                        {},
                        research_model=research_model,
                        segment=segment,
                    )
                    if segment
                    else {}
                )
            payload["factor_reliability"] = {}
            payload["reliability_scope"] = "per_fund_peer_group"
        else:
            payload["factor_reliability"] = factor_reliability(
                ic_context.get("factors") or None
            )
            payload["reliability_scope"] = "global_legacy"
    except Exception:  # noqa: BLE001 — IC 置信缺失不应影响因子分主体
        payload.setdefault("factor_reliability", {})
    payload["ic_status"] = status
    return payload


@app.get("/api/portfolio/evidence-overview")
def portfolio_evidence_overview() -> dict:
    """组合层证据总览（懒加载）：每只持仓三路量化置信聚合 → 组合级背书分布。

    模块4 证据卡延伸；设计见
    docs/superpowers/specs/2026-06-24-evidence-overview-design.md。
    """
    from app.services.portfolio_snapshot import build_evidence_overview_payload

    holdings, *_ = load_persisted_holdings()
    try:
        return build_evidence_overview_payload(holdings)
    except Exception:  # noqa: BLE001 — best-effort，不应 500
        return {"available": False, "overview": {"available": False}, "holdings": []}


def _portfolio_holdings_sync() -> dict:
    user_key = str(get_request_user_id())
    request_generation = get_holdings_cache_generation()

    def _response_from_holdings(
        holdings: list[Holding],
        *,
        source: str,
        snapshot_date: str | None,
        refreshed_at: datetime | None,
    ) -> dict:
        enriched = apply_server_sector_cache_to_holdings(holdings, network_fallback=False)
        return build_portfolio_holdings_response(
            enriched,
            source=source,
            snapshot_date=snapshot_date,
            refreshed_at=refreshed_at,
            fetch_benchmark=False,
        )

    cached = get_cached_holdings_response()
    if cached is not None:
        return cached

    fast_snapshot = build_fast_snapshot_holdings_response()
    if fast_snapshot is not None:
        if not save_cached_holdings_response(
            fast_snapshot,
            expected_generation=request_generation,
        ):
            return get_cached_holdings_response() or fast_snapshot
        schedule_warm_holdings_intraday(
            [Holding.model_validate(item) for item in fast_snapshot.get("holdings", [])],
            user_key=user_key,
            user_id=int(user_key) if user_key.isdigit() else None,
            portfolio_summary=(
                PortfolioSummary.model_validate(fast_snapshot["portfolio_summary"])
                if fast_snapshot.get("portfolio_summary")
                else None
            ),
        )
        return fast_snapshot

    holdings, source, snapshot_date, refreshed_at = load_persisted_holdings(
        fetch_benchmark=False,
    )
    payload = _response_from_holdings(
        holdings,
        source=source,
        snapshot_date=snapshot_date,
        refreshed_at=refreshed_at,
    )
    if not save_cached_holdings_response(payload, expected_generation=request_generation):
        return get_cached_holdings_response() or payload
    schedule_warm_holdings_intraday(
        [Holding.model_validate(item) for item in payload.get("holdings", [])],
        user_key=user_key,
        user_id=int(user_key) if user_key.isdigit() else None,
        portfolio_summary=(
            PortfolioSummary.model_validate(payload["portfolio_summary"])
            if payload.get("portfolio_summary")
            else None
        ),
    )
    return payload


@app.get("/api/portfolio/holdings")
async def portfolio_holdings() -> dict:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_portfolio_holdings_sync),
            timeout=HOLDINGS_READ_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="持仓加载超时，请稍后重试") from exc


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


