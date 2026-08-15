"""基金净值历史：全用户共享缓存（按 fund_code）。

官方净值每个交易日最多更新一次，且休市不会变。缓存是否过期看
``latest_date`` 是否覆盖 ``needed_official_nav_date``，而不是短 TTL。
请求侧按 trading_days 切片，后台始终拉一份足够长的序列。
"""

from __future__ import annotations

from app.models import FundNavHistory
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import needed_official_nav_date

_CACHE_VERSION = "v2"
_LEGACY_CACHE_VERSION = "v1"
# 覆盖详情 252 日与净值列表分页 pool 800 日。
CANONICAL_NAV_TRADING_DAYS = 800
# 供请求路径长期复用；是否重拉由 needed_official_nav_date 决定。
_SERVE_TTL_SECONDS = 86400.0 * 7
_LEGACY_NAV_WINDOWS = (90, 252, 800)


def nav_cache_key(fund_code: str, trading_days: int = 0) -> str:
    _ = trading_days
    return f"fund:nav:{_CACHE_VERSION}:{fund_code}"


def _legacy_v1_nav_cache_key(fund_code: str, trading_days: int) -> str:
    return f"fund:nav:{_LEGACY_CACHE_VERSION}:{fund_code}:{trading_days}"


def _legacy_v1_payload(fund_code: str, trading_days: int) -> dict | None:
    """读升级前按 days 分片的 v1 key，避免冷切 v2 后详情空白。"""
    windows: list[int] = []
    for days in (trading_days, *_LEGACY_NAV_WINDOWS):
        if days > 0 and days not in windows:
            windows.append(days)
    best: dict | None = None
    best_n = -1
    for days in windows:
        payload = get_spot_snapshot(
            _legacy_v1_nav_cache_key(fund_code, days),
            ttl_seconds=_SERVE_TTL_SECONDS,
        )
        if not payload:
            continue
        count = len(payload.get("points") or [])
        if count > best_n:
            best = payload
            best_n = count
    return best


def nav_covers_needed_date(history: FundNavHistory | None, needed_date: str) -> bool:
    if history is None or not history.points:
        return False
    latest = str(history.latest_date or "").strip()
    return bool(latest) and latest >= needed_date


def _slice_history(history: FundNavHistory, trading_days: int) -> FundNavHistory:
    if trading_days <= 0 or len(history.points) <= trading_days:
        return history
    points = sorted(history.points, key=lambda point: point.date)[-trading_days:]
    latest = points[-1]
    period_change = None
    if points[0].nav > 0:
        period_change = round((latest.nav / points[0].nav - 1) * 100, 2)
    return history.model_copy(
        update={
            "points": points,
            "latest_nav": latest.nav,
            "latest_date": latest.date,
            "period_change_percent": period_change,
        }
    )


def get_cached_fund_nav(fund_code: str, trading_days: int) -> FundNavHistory | None:
    payload = get_spot_snapshot(
        nav_cache_key(fund_code),
        ttl_seconds=_SERVE_TTL_SECONDS,
    )
    if not payload:
        payload = _legacy_v1_payload(fund_code, trading_days)
    if not payload:
        return None
    try:
        history = FundNavHistory.model_validate(payload)
    except Exception:
        return None
    return _slice_history(history, trading_days)


def save_cached_fund_nav(
    fund_code: str,
    trading_days: int,
    history: FundNavHistory,
) -> None:
    _ = trading_days
    if history.source != "akshare" or not history.points:
        return
    existing = get_spot_snapshot(nav_cache_key(fund_code), ttl_seconds=_SERVE_TTL_SECONDS)
    if existing:
        try:
            previous = FundNavHistory.model_validate(existing)
        except Exception:
            previous = None
        if (
            previous is not None
            and len(previous.points) > len(history.points)
            and nav_covers_needed_date(previous, str(history.latest_date or ""))
        ):
            return
    save_spot_snapshot(
        nav_cache_key(fund_code),
        history.model_dump(mode="json"),
    )


def warm_fund_nav(
    fund_code: str,
    fund_name: str = "",
    *,
    trading_days: int = CANONICAL_NAV_TRADING_DAYS,
) -> bool:
    """Best-effort 预热单只基金净值缓存。已覆盖所需披露日则跳过。"""
    if not fund_code or fund_code == "000000":
        return False
    cached = get_cached_fund_nav(fund_code, trading_days)
    if nav_covers_needed_date(cached, needed_official_nav_date()):
        return True
    from app.services.fund_data import FundDataService

    history = FundDataService().get_nav_history(
        fund_code,
        fund_name,
        trading_days=max(trading_days, CANONICAL_NAV_TRADING_DAYS),
        cache_only=False,
    )
    return history.source == "akshare" and bool(history.points)
