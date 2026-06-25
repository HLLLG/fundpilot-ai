from __future__ import annotations

import logging
import threading
import time

from app.config import get_settings
from app.database import get_fund_profile_by_code
from app.models import Holding
from app.services.portfolio_profit_analysis import _resolve_intraday_for_holding
from app.services.sector_intraday_provider import fetch_sector_intraday
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_WARMUP_LOCK = threading.Lock()
_LAST_WARMUP_AT: dict[str, float] = {}
_MIN_WARMUP_INTERVAL_SECONDS = 120.0


def collect_intraday_queries(holdings: list[Holding]) -> list[tuple[str, str]]:
    """去重后的 (source_type, source_name) 列表，供后台预热板块分时。"""
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for holding in holdings:
        if not holding.fund_code or holding.fund_code == "000000":
            continue
        profile = get_fund_profile_by_code(holding.fund_code)
        query = _resolve_intraday_for_holding(holding, profile)
        if query is None:
            continue
        if query in seen:
            continue
        seen.add(query)
        ordered.append(query)
    return ordered


def warm_holdings_intraday(holdings: list[Holding], *, user_key: str = "global") -> int:
    """Best-effort 预热持仓关联板块分时（走服务端全局 intraday 缓存，非 force_refresh）。"""
    if not get_settings().sector_quotes_enabled:
        return 0
    if not holdings:
        return 0

    queries = collect_intraday_queries(holdings)
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


def schedule_warm_holdings_intraday(holdings: list[Holding], *, user_key: str | None = None) -> None:
    """防抖后后台线程预热，避免持仓页连点触发多次东财请求。"""
    settings = get_settings()
    if not getattr(settings, "holding_intraday_warmup_enabled", True):
        return
    if not holdings:
        return

    key = user_key or "anonymous"
    now = time.monotonic()
    with _WARMUP_LOCK:
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

    def _run() -> None:
        try:
            warm_holdings_intraday(snapshot, user_key=key)
        except Exception:  # noqa: BLE001
            logger.debug("intraday warmup thread failed", exc_info=True)

    threading.Thread(
        target=_run,
        name=f"holding-intraday-warmup-{key}",
        daemon=True,
    ).start()
