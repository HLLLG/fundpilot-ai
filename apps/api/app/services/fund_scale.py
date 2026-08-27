"""规模口径：优先季报净资产，否则用最新净值 × 上季份额。

季报净资产 = 报告期单位净值 × 最近披露总份额，对齐天天基金「基金规模」。
新浪开放式大类表没有期末净资产列，但有「最近总份额」和当日单位净值。
定时任务整包写入 `fund_research_profile`：能取到报告期净值就写季报净资产；
否则用当日净值 × 上季份额兜底，标 `nav_times_latest_shares`，算出来立刻写库。
荐基富化算出更准的数也立刻覆盖表行，不等下一季、不等下一轮定时任务。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

QUARTERLY_NET_ASSETS_BASIS = "quarterly_net_assets"
NAV_TIMES_LATEST_SHARES_BASIS = "nav_times_latest_shares"
_MAX_NAV_LOOKBACK_DAYS = 7

# 法定最晚披露日。中报法定 8/31，新浪/天天基金通常 8 月中旬已齐，故可用日取 8/15。
_QUARTER_FILINGS: tuple[tuple[int, int, int, int, int], ...] = (
    (3, 31, 4, 22, 0),
    (6, 30, 8, 15, 0),
    (9, 30, 10, 27, 0),
    (12, 31, 3, 31, 1),
)


def _parse_iso_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _finite_positive(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    return number


def latest_disclosed_quarter_end(as_of: date | None = None) -> date:
    """返回 as_of 当天按披露节奏已可使用的最近季报截止日。"""

    today = as_of or date.today()
    found: date | None = None
    for year in (today.year, today.year - 1, today.year - 2):
        for month, day, avail_month, avail_day, year_offset in _QUARTER_FILINGS:
            try:
                report = date(year, month, day)
                available = date(year + year_offset, avail_month, avail_day)
            except ValueError:
                continue
            if available <= today and (found is None or report > found):
                found = report
    if found is None:
        return date(today.year - 1, 12, 31)
    return found


def nav_on_or_before(
    points: list[Any] | None,
    report_date: date,
    *,
    max_lookback_days: int = _MAX_NAV_LOOKBACK_DAYS,
) -> tuple[date, float] | None:
    """取报告日当天或之前最近一个交易日的单位净值，避免节假日/QDII 晚一天空窗。"""

    cutoff = report_date - timedelta(days=max(0, max_lookback_days))
    best: tuple[date, float] | None = None
    for raw in points or []:
        parsed = _nav_point(raw)
        if parsed is None:
            continue
        nav_date, nav = parsed
        if nav_date > report_date or nav_date < cutoff:
            continue
        if best is None or nav_date > best[0]:
            best = (nav_date, nav)
    return best


def latest_nav_from_points(points: list[Any] | None) -> tuple[date, float] | None:
    best: tuple[date, float] | None = None
    for raw in points or []:
        parsed = _nav_point(raw)
        if parsed is None:
            continue
        nav_date, nav = parsed
        if best is None or nav_date > best[0]:
            best = (nav_date, nav)
    return best


def _nav_point(raw: Any) -> tuple[date, float] | None:
    if isinstance(raw, dict):
        nav_date = _parse_iso_date(raw.get("date") or raw.get("净值日期"))
        nav = _finite_positive(raw.get("nav") or raw.get("单位净值"))
    else:
        nav_date = _parse_iso_date(getattr(raw, "date", None))
        nav = _finite_positive(getattr(raw, "nav", None))
    if nav_date is None or nav is None:
        return None
    return nav_date, nav


def quarterly_net_assets_yi(shares_yi: object, nav: object) -> float | None:
    shares = _finite_positive(shares_yi)
    unit_nav = _finite_positive(nav)
    if shares is None or unit_nav is None:
        return None
    return round(shares * unit_nav, 4)


def _scale_payload(
    shares_yi: object,
    nav: object,
    *,
    basis: str,
    nav_date: date | None = None,
    report_date: date | None = None,
) -> dict[str, Any] | None:
    value = quarterly_net_assets_yi(shares_yi, nav)
    if value is None:
        return None
    payload: dict[str, Any] = {
        "fund_scale_yi": value,
        "fund_scale_basis": basis,
    }
    if nav_date is not None:
        payload["fund_scale_as_of"] = nav_date.isoformat()
    if report_date is not None:
        payload["fund_scale_report_date"] = report_date.isoformat()
    return payload


def quarterly_net_assets_from_points(
    shares_yi: object,
    points: list[Any] | None,
    *,
    as_of: date | None = None,
) -> dict[str, Any] | None:
    report_date = latest_disclosed_quarter_end(as_of)
    matched = nav_on_or_before(points, report_date)
    if matched is None:
        return None
    nav_date, nav = matched
    return _scale_payload(
        shares_yi,
        nav,
        basis=QUARTERLY_NET_ASSETS_BASIS,
        nav_date=nav_date,
        report_date=report_date,
    )


def latest_nav_times_shares(
    shares_yi: object,
    nav: object,
    *,
    nav_date: date | None = None,
) -> dict[str, Any] | None:
    return _scale_payload(
        shares_yi,
        nav,
        basis=NAV_TIMES_LATEST_SHARES_BASIS,
        nav_date=nav_date,
    )


def _latest_nav_fallback(
    shares_yi: object,
    points: list[Any] | None,
    *,
    latest_nav: object = None,
    latest_nav_date: object = None,
) -> dict[str, Any] | None:
    nav = _finite_positive(latest_nav)
    nav_date = _parse_iso_date(latest_nav_date)
    if nav is None:
        matched = latest_nav_from_points(points)
        if matched is None:
            return None
        nav_date, nav = matched
    return latest_nav_times_shares(shares_yi, nav, nav_date=nav_date)


def _has_quarterly_scale(row: dict[str, Any]) -> bool:
    return (
        row.get("fund_scale_basis") == QUARTERLY_NET_ASSETS_BASIS
        and _finite_positive(row.get("fund_scale_yi")) is not None
    )


def profile_has_scale_input(row: dict[str, Any] | None) -> bool:
    item = row or {}
    if _finite_positive(item.get("fund_scale_yi")) is not None:
        return True
    return _finite_positive(item.get("fund_shares_yi")) is not None


def apply_quarterly_net_assets_to_row(
    row: dict[str, Any],
    *,
    shares_yi: object = None,
    points: list[Any] | None = None,
    as_of: date | None = None,
    latest_nav: object = None,
) -> dict[str, Any]:
    """有报告期净值则覆盖为季报净资产；否则用最新净值×份额，但不降级已有季报数。"""

    shares = shares_yi if shares_yi is not None else row.get("fund_shares_yi")
    computed = quarterly_net_assets_from_points(shares, points, as_of=as_of)
    if computed is not None:
        row.update(computed)
        return row
    if _has_quarterly_scale(row):
        return row
    fallback = _latest_nav_fallback(
        shares,
        points,
        latest_nav=latest_nav if latest_nav is not None else row.get("latest_nav"),
        latest_nav_date=row.get("fund_scale_as_of") or row.get("profile_updated_at"),
    )
    if fallback is not None:
        row.update(fallback)
    return row


def attach_quarterly_net_assets(
    rows: list[dict[str, Any]],
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """给新浪整包行补规模。优先读净值缓存算季报净资产，否则用行上当日净值。"""

    from app.services.fund_nav_cache import CANONICAL_NAV_TRADING_DAYS, get_cached_fund_nav

    today = as_of or date.today()
    for row in rows:
        if not isinstance(row, dict):
            continue
        shares = _finite_positive(row.get("fund_shares_yi"))
        if shares is None:
            continue
        code = str(row.get("fund_code") or "").zfill(6)
        history = get_cached_fund_nav(code, CANONICAL_NAV_TRADING_DAYS)
        points = list(getattr(history, "points", None) or []) if history else []
        computed = quarterly_net_assets_from_points(shares, points, as_of=today)
        if computed is not None:
            row.update(computed)
            continue
        fallback = _latest_nav_fallback(
            shares,
            points,
            latest_nav=row.get("latest_nav"),
            latest_nav_date=row.get("profile_updated_at"),
        )
        if fallback is not None:
            row.update(fallback)
            continue
        row["fund_scale_yi"] = None
        row["fund_scale_basis"] = None
    return rows
