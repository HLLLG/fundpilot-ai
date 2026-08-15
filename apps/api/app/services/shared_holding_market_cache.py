"""全用户共享的净值 / 分时缓存时钟。

净值：每个交易日官方披露后才变；休市或盘中重复拉取是徒劳。
分时：只在连续竞价时段刷新；午休、收盘后、周末拉取是徒劳。
请求路径只读这些缓存，不在用户点击时打东财 / AkShare。
"""

from __future__ import annotations

import logging
import threading
import time

from app.config import get_settings
from app.database import list_distinct_portfolio_user_ids
from app.models import Holding
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.fund_nav_cache import (
    CANONICAL_NAV_TRADING_DAYS,
    get_cached_fund_nav,
    nav_covers_needed_date,
    warm_fund_nav,
)
from app.services.holding_intraday_warmup import (
    collect_intraday_queries,
    collect_unique_fund_codes,
    warm_holdings_intraday,
)
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.trading_session import (
    build_trading_session,
    needed_official_nav_date,
    should_refresh_intraday_charts,
)

logger = logging.getLogger(__name__)

_POLL_SECONDS = 15.0
_DETAIL_NAV_TRADING_DAYS = CANONICAL_NAV_TRADING_DAYS
_JOB_LOCK = threading.Lock()
_LAST_NAV_FETCH_AT = 0.0
_LAST_INTRADAY_FETCH_AT = 0.0
_LAST_INTRADAY_FINALIZE_DATE: str | None = None


def _nav_interval_seconds() -> float:
    return float(max(60, int(get_settings().holding_nav_refresh_interval_seconds)))


def _intraday_interval_seconds() -> float:
    return float(max(60, int(get_settings().holding_intraday_refresh_interval_seconds)))


def collect_all_user_holdings() -> list[Holding]:
    holdings: list[Holding] = []
    for user_id in list_distinct_portfolio_user_ids():
        token = set_request_user_id(user_id)
        try:
            user_holdings, *_rest = load_persisted_holdings(fetch_benchmark=False)
            holdings.extend(user_holdings)
        except Exception as exc:  # noqa: BLE001
            logger.info("shared market cache load holdings user=%s failed: %s", user_id, exc)
        finally:
            reset_request_user_id(token)
    return holdings


def _pending_nav_codes(
    holdings: list[Holding],
    *,
    needed_date: str,
    trading_days: int = _DETAIL_NAV_TRADING_DAYS,
) -> tuple[list[str], list[str]]:
    """返回 (完全没有缓存的代码, 有缓存但未覆盖所需披露日的代码)。"""
    missing: list[str] = []
    stale: list[str] = []
    seen: set[str] = set()
    for fund_code in collect_unique_fund_codes(holdings):
        if fund_code in seen:
            continue
        seen.add(fund_code)
        cached = get_cached_fund_nav(fund_code, trading_days)
        if nav_covers_needed_date(cached, needed_date):
            continue
        if cached is None or not cached.points:
            missing.append(fund_code)
        else:
            stale.append(fund_code)
    return missing, stale


def refresh_shared_nav_histories(
    holdings: list[Holding] | None = None,
    *,
    session: dict | None = None,
    now: float | None = None,
    min_interval_seconds: float | None = None,
) -> dict[str, object]:
    """按披露窗口刷新共享净值。已覆盖所需日期则跳过；收盘后按间隔重试。"""
    global _LAST_NAV_FETCH_AT
    current = session or build_trading_session()
    needed_date = needed_official_nav_date(current)
    snapshot = holdings if holdings is not None else collect_all_user_holdings()
    missing, stale = _pending_nav_codes(snapshot, needed_date=needed_date)
    if not missing and not stale:
        return {
            "fetched": 0,
            "skipped": True,
            "reason": "covers_needed_date",
            "needed_date": needed_date,
        }

    interval = (
        float(min_interval_seconds)
        if min_interval_seconds is not None
        else _nav_interval_seconds()
    )
    monotonic_now = time.monotonic() if now is None else now
    interval_ok = _LAST_NAV_FETCH_AT <= 0 or monotonic_now - _LAST_NAV_FETCH_AT >= interval
    # 冷缓存立刻补；已有旧净值则按间隔重试（收盘后等当日披露；休市缺口也只按间隔补一次）。
    fetch_codes = list(missing)
    if stale and interval_ok:
        fetch_codes.extend(stale)

    if not fetch_codes:
        return {
            "fetched": 0,
            "skipped": True,
            "reason": "nav_retry_throttled",
            "needed_date": needed_date,
            "pending_stale": len(stale),
        }

    names = {
        str(holding.fund_code): str(holding.fund_name or "")
        for holding in snapshot
        if holding.fund_code
    }
    fetched = 0
    for fund_code in dict.fromkeys(fetch_codes):
        try:
            if warm_fund_nav(
                fund_code,
                names.get(fund_code, ""),
                trading_days=_DETAIL_NAV_TRADING_DAYS,
            ):
                fetched += 1
        except Exception:  # noqa: BLE001
            logger.debug("shared nav refresh failed for %s", fund_code, exc_info=True)
        time.sleep(0.2)
    _LAST_NAV_FETCH_AT = monotonic_now
    return {
        "fetched": fetched,
        "skipped": False,
        "reason": "refreshed",
        "needed_date": needed_date,
        "attempted": len(dict.fromkeys(fetch_codes)),
    }


def refresh_shared_intraday_charts(
    holdings: list[Holding] | None = None,
    *,
    session: dict | None = None,
    now: float | None = None,
    min_interval_seconds: float | None = None,
    force: bool = False,
) -> dict[str, object]:
    """仅连续竞价时段刷新共享分时；收盘后补一次 15:00 定稿，休市不再拉。"""
    global _LAST_INTRADAY_FETCH_AT, _LAST_INTRADAY_FINALIZE_DATE
    current = session or build_trading_session()
    live = should_refresh_intraday_charts(current)
    after_close = str(current.get("session_kind") or "") == "trading_day_after_close"
    if not live and not after_close:
        return {"fetched": 0, "skipped": True, "reason": "session_closed"}
    if not get_settings().sector_quotes_enabled:
        return {"fetched": 0, "skipped": True, "reason": "sector_quotes_disabled"}

    calendar_date = str(current.get("calendar_date") or "")
    if after_close and not live and not force:
        if _LAST_INTRADAY_FINALIZE_DATE == calendar_date:
            return {"fetched": 0, "skipped": True, "reason": "intraday_finalized"}

    interval = (
        float(min_interval_seconds)
        if min_interval_seconds is not None
        else _intraday_interval_seconds()
    )
    monotonic_now = time.monotonic() if now is None else now
    if live and not force and _LAST_INTRADAY_FETCH_AT > 0 and monotonic_now - _LAST_INTRADAY_FETCH_AT < interval:
        return {"fetched": 0, "skipped": True, "reason": "intraday_throttled"}

    snapshot = holdings if holdings is not None else collect_all_user_holdings()
    fetched = warm_holdings_intraday(snapshot, force_refresh=True)
    _LAST_INTRADAY_FETCH_AT = monotonic_now
    if after_close:
        _LAST_INTRADAY_FINALIZE_DATE = calendar_date
    queries = collect_intraday_queries(snapshot)
    return {
        "fetched": fetched,
        "skipped": False,
        "reason": "refreshed",
        "queries": len(queries),
    }


def run_shared_holding_market_cache_once(*, force_intraday: bool = False) -> dict[str, object]:
    if not get_settings().holding_intraday_warmup_enabled:
        return {"nav": {"skipped": True, "reason": "disabled"}, "intraday": {"skipped": True, "reason": "disabled"}}
    if not _JOB_LOCK.acquire(blocking=False):
        return {"nav": {"skipped": True, "reason": "in_flight"}, "intraday": {"skipped": True, "reason": "in_flight"}}
    try:
        session = build_trading_session()
        holdings = collect_all_user_holdings()
        try:
            from app.services.sector_quote_cache import maybe_prune_durable_caches

            maybe_prune_durable_caches()
        except Exception:
            logger.debug("durable cache prune skipped", exc_info=True)
        nav = refresh_shared_nav_histories(holdings, session=session)
        intraday = refresh_shared_intraday_charts(
            holdings,
            session=session,
            force=force_intraday,
        )
        return {"nav": nav, "intraday": intraday, "holdings": len(holdings)}
    finally:
        _JOB_LOCK.release()


def shared_holding_market_cache_loop() -> None:
    """daemon：启动补齐缺口；之后按时段轮询分时 / 收盘后净值。"""
    if not get_settings().holding_intraday_warmup_enabled:
        return
    try:
        run_shared_holding_market_cache_once(force_intraday=True)
    except Exception as exc:  # noqa: BLE001
        logger.info("shared holding market cache initial refresh failed: %s", exc)

    while True:
        time.sleep(_POLL_SECONDS)
        if not get_settings().holding_intraday_warmup_enabled:
            continue
        try:
            run_shared_holding_market_cache_once()
        except Exception as exc:  # noqa: BLE001
            logger.info("shared holding market cache refresh failed: %s", exc)
