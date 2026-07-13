from __future__ import annotations

import logging
import threading
import time

from app.config import get_settings
from app.models import Holding, PortfolioSummary, SectorQuoteMeta
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.fund_nav_cache import warm_fund_nav
from app.services.fund_profile import FundProfileService
from app.services.holding_detail_cache import (
    get_cached_holding_detail,
    holding_detail_fingerprint,
    save_cached_holding_detail,
)
from app.services.holding_detail_service import HoldingDetailDataContext, build_holding_detail
from app.services.portfolio_profit_analysis import _resolve_intraday_for_holding
from app.services.sector_intraday_provider import fetch_sector_intraday
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_WARMUP_LOCK = threading.Lock()
_LAST_WARMUP_AT: dict[str, float] = {}
_MIN_WARMUP_INTERVAL_SECONDS = 120.0
_WARMUP_STATE_TTL_SECONDS = 3600.0
_MAX_WARMUP_KEYS = 1024
_DETAIL_NAV_TRADING_DAYS = 252


def collect_unique_fund_codes(holdings: list[Holding]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for holding in holdings:
        code = str(holding.fund_code or "").strip()
        if not code or code == "000000" or code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    return ordered


def collect_intraday_queries(
    holdings: list[Holding],
    *,
    data_context: HoldingDetailDataContext | None = None,
) -> list[tuple[str, str]]:
    """去重后的 (source_type, source_name) 列表，供后台预热板块分时。"""
    eligible = [
        holding
        for holding in holdings
        if holding.fund_code and holding.fund_code != "000000"
    ]
    if not eligible:
        return []

    context = data_context or HoldingDetailDataContext()
    context.preload_profiles()
    profile_service = FundProfileService()
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for holding in eligible:
        profile = context.find_profile(holding, profile_service)
        query = _resolve_intraday_for_holding(holding, profile)
        if query is None:
            continue
        if query in seen:
            continue
        seen.add(query)
        ordered.append(query)
    return ordered


def warm_fund_nav_histories(
    holdings: list[Holding],
    *,
    trading_days: int = _DETAIL_NAV_TRADING_DAYS,
) -> int:
    """Best-effort 预热基金净值（全用户共享 fund_nav_cache）。"""
    warmed = 0
    code_to_name = {
        str(h.fund_code): str(h.fund_name or "")
        for h in holdings
        if h.fund_code and h.fund_code != "000000"
    }
    for fund_code in collect_unique_fund_codes(holdings):
        try:
            if warm_fund_nav(
                fund_code,
                code_to_name.get(fund_code, ""),
                trading_days=trading_days,
            ):
                warmed += 1
        except Exception:  # noqa: BLE001
            logger.debug("fund nav warmup failed for %s", fund_code, exc_info=True)
        time.sleep(0.2)
    return warmed


def warm_holdings_intraday(
    holdings: list[Holding],
    *,
    user_key: str = "global",
    data_context: HoldingDetailDataContext | None = None,
) -> int:
    """Best-effort 预热持仓关联板块分时（走服务端全局 intraday 缓存，非 force_refresh）。"""
    if not get_settings().sector_quotes_enabled:
        return 0
    if not holdings:
        return 0

    try:
        queries = collect_intraday_queries(holdings, data_context=data_context)
    except Exception:  # noqa: BLE001
        logger.debug("intraday profile preload failed", exc_info=True)
        return 0
    if not queries:
        return 0

    warmed = 0
    for source_type, source_name in queries:
        try:
            points, *_rest = fetch_sector_intraday(
                source_type,
                source_name,
                force_refresh=False,
            )
            if points:
                warmed += 1
        except Exception:  # noqa: BLE001 — 预热失败不阻塞主流程
            logger.debug(
                "intraday warmup failed for %s:%s",
                source_type,
                source_name,
                exc_info=True,
            )
        time.sleep(0.15)
    logger.debug("intraday warmup user=%s queries=%d warmed=%d", user_key, len(queries), warmed)
    return warmed


def warm_holding_details(
    holdings: list[Holding],
    *,
    user_id: int,
    portfolio_summary: PortfolioSummary | None = None,
    sector_quote_meta: SectorQuoteMeta | None = None,
    data_context: HoldingDetailDataContext | None = None,
) -> int:
    """Best-effort 预热用户级 holding detail 缓存。"""
    if not holdings:
        return 0

    token = set_request_user_id(user_id)
    warmed = 0
    try:
        pending: list[tuple[int, Holding, str]] = []
        for index, holding in enumerate(holdings):
            if not holding.fund_code or holding.fund_code == "000000":
                continue
            fingerprint = holding_detail_fingerprint(
                fund_code=holding.fund_code,
                holding_amount=holding.holding_amount,
            )
            if get_cached_holding_detail(holding.fund_code, fingerprint) is not None:
                continue
            pending.append((index, holding, fingerprint))

        if not pending:
            return 0

        context = data_context or HoldingDetailDataContext()
        try:
            context.preload_profiles()
        except Exception:  # noqa: BLE001
            logger.debug("holding detail profile preload failed", exc_info=True)
        try:
            context.preload_primary_sectors(holdings)
        except Exception:  # noqa: BLE001
            logger.debug("holding detail primary-sector preload failed", exc_info=True)
        try:
            context.preload_snapshots()
        except Exception:  # noqa: BLE001
            logger.debug("holding detail snapshot preload failed", exc_info=True)

        for index, holding, fingerprint in pending:
            if get_cached_holding_detail(holding.fund_code, fingerprint) is not None:
                continue
            try:
                detail = build_holding_detail(
                    holdings,
                    index,
                    portfolio_summary=portfolio_summary,
                    sector_quote_meta=sector_quote_meta,
                    data_context=context,
                )
                save_cached_holding_detail(
                    holding.fund_code,
                    fingerprint,
                    detail.model_dump(mode="json"),
                )
                warmed += 1
            except Exception:  # noqa: BLE001
                logger.debug(
                    "holding detail warmup failed index=%s code=%s",
                    index,
                    holding.fund_code,
                    exc_info=True,
                )
            time.sleep(0.15)
    finally:
        reset_request_user_id(token)
    return warmed


def warm_holdings_cache(
    holdings: list[Holding],
    *,
    user_id: int | None = None,
    user_key: str = "global",
    portfolio_summary: PortfolioSummary | None = None,
) -> dict[str, int]:
    """分时（基金级）+ 净值（基金级）+ 详情（用户级）三层预热。"""
    token = set_request_user_id(user_id) if user_id is not None else None
    data_context = HoldingDetailDataContext()
    try:
        nav_warmed = warm_fund_nav_histories(holdings)
        intraday_warmed = warm_holdings_intraday(
            holdings,
            user_key=user_key,
            data_context=data_context,
        )
        detail_warmed = 0
        if user_id is not None:
            detail_warmed = warm_holding_details(
                holdings,
                user_id=user_id,
                portfolio_summary=portfolio_summary,
                data_context=data_context,
            )
        return {
            "nav": nav_warmed,
            "intraday": intraday_warmed,
            "detail": detail_warmed,
        }
    finally:
        if token is not None:
            reset_request_user_id(token)


def _prune_warmup_state(now: float, *, incoming_key: str) -> None:
    stale_keys = [
        key
        for key, warmed_at in _LAST_WARMUP_AT.items()
        if now - warmed_at >= _WARMUP_STATE_TTL_SECONDS
    ]
    for key in stale_keys:
        _LAST_WARMUP_AT.pop(key, None)

    capacity = max(1, _MAX_WARMUP_KEYS)
    reserve = 0 if incoming_key in _LAST_WARMUP_AT else 1
    overflow = len(_LAST_WARMUP_AT) + reserve - capacity
    if overflow <= 0:
        return
    eviction_candidates = sorted(
        (
            (key, warmed_at)
            for key, warmed_at in _LAST_WARMUP_AT.items()
            if key != incoming_key
        ),
        key=lambda item: item[1],
    )
    for key, _warmed_at in eviction_candidates[:overflow]:
        _LAST_WARMUP_AT.pop(key, None)


def schedule_warm_holdings_intraday(
    holdings: list[Holding],
    *,
    user_key: str | None = None,
    user_id: int | None = None,
    portfolio_summary: PortfolioSummary | None = None,
) -> None:
    """防抖后后台线程预热持仓缓存（分时 + 净值 + 详情）。"""
    settings = get_settings()
    if not getattr(settings, "holding_intraday_warmup_enabled", True):
        return
    if not holdings:
        return

    key = user_key or (str(user_id) if user_id is not None else "anonymous")
    resolved_user_id = user_id
    if resolved_user_id is None and key.isdigit():
        resolved_user_id = int(key)

    now = time.monotonic()
    with _WARMUP_LOCK:
        _prune_warmup_state(now, incoming_key=key)
        last = _LAST_WARMUP_AT.get(key, 0.0)
        session = build_trading_session()
        interval = _MIN_WARMUP_INTERVAL_SECONDS
        if session.get("session_kind") not in {
            "trading_day_intraday",
            "trading_day_pre_close",
        }:
            interval = max(interval, 600.0)
        if now - last < interval:
            return
        _LAST_WARMUP_AT[key] = now

    snapshot = [holding.model_copy(deep=True) for holding in holdings]
    summary_copy = (
        portfolio_summary.model_copy(deep=True) if portfolio_summary is not None else None
    )

    def _run() -> None:
        try:
            warm_holdings_cache(
                snapshot,
                user_id=resolved_user_id,
                user_key=key,
                portfolio_summary=summary_copy,
            )
        except Exception:  # noqa: BLE001
            logger.debug("holdings cache warmup thread failed", exc_info=True)

    threading.Thread(
        target=_run,
        name=f"holding-cache-warmup-{key}",
        daemon=True,
    ).start()
