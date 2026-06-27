"""持仓板块涨跌后台刷新：每 3min（可配置）拉取共享现货板并写回各用户日快照。"""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.database import list_distinct_portfolio_user_ids
from app.models import Holding
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.portfolio_persistence import persist_holdings_after_sector_refresh
from app.services.sector_quote_provider import fetch_spot_boards_result
from app.services.sector_quote_service import refresh_holdings_sector_quotes
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_INTRADAY_SESSIONS = frozenset({"trading_day_intraday", "trading_day_pre_close"})
_POLL_SECONDS = 30.0


def _refresh_enabled() -> bool:
    return bool(get_settings().sector_quotes_enabled)


def _interval_seconds() -> float:
    return float(max(60, int(get_settings().sector_quotes_auto_interval_seconds)))


def _auto_refresh_allowed() -> bool:
    session_kind = build_trading_session().get("session_kind", "")
    return session_kind in _INTRADAY_SESSIONS


def refresh_shared_spot_boards(*, force_refresh: bool = True) -> None:
    fetch_spot_boards_result(force_refresh=force_refresh, timeout_seconds=12.0)


def refresh_portfolio_sectors_for_user(user_id: int) -> None:
    token = set_request_user_id(user_id)
    try:
        holdings, *_ = load_persisted_holdings(fetch_benchmark=False)
        if not holdings:
            return
        result = refresh_holdings_sector_quotes(holdings, cache_only=True)
        if not result.get("holdings"):
            return
        refreshed = [Holding.model_validate(item) for item in result["holdings"]]
        persist_holdings_after_sector_refresh(refreshed, with_official_nav=False)
    finally:
        reset_request_user_id(token)


def refresh_all_portfolio_sectors() -> None:
    if not _refresh_enabled():
        return
    try:
        refresh_shared_spot_boards(force_refresh=True)
    except Exception as exc:
        logger.info("portfolio sector spot boards refresh failed: %s", exc)

    for user_id in list_distinct_portfolio_user_ids():
        try:
            refresh_portfolio_sectors_for_user(user_id)
        except Exception as exc:
            logger.info("portfolio sector refresh user=%s failed: %s", user_id, exc)


def portfolio_sector_refresh_loop() -> None:
    """daemon：启动预热；盘中每 auto_interval 秒刷新共享板块并写回各用户快照。"""
    if not _refresh_enabled():
        return

    try:
        refresh_all_portfolio_sectors()
    except Exception as exc:
        logger.info("portfolio sector initial refresh failed: %s", exc)

    last_refresh_at = time.monotonic()
    while True:
        time.sleep(_POLL_SECONDS)
        if not _auto_refresh_allowed():
            continue
        now = time.monotonic()
        if now - last_refresh_at < _interval_seconds():
            continue
        try:
            refresh_all_portfolio_sectors()
            last_refresh_at = now
            logger.debug("portfolio sector refresh done interval=%ss", int(_interval_seconds()))
        except Exception as exc:
            logger.info("portfolio sector refresh failed: %s", exc)
