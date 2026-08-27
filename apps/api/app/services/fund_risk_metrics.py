"""身份集自算夏普 / 一年回撤：只写净值复算，不接雪球现成数字。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.database import (
    list_fresh_verified_identity_fund_codes,
    list_fund_risk_metrics,
    list_fund_risk_metrics_by_codes,
    upsert_fund_risk_metrics,
)
from app.services.fund_nav_cache import CANONICAL_NAV_TRADING_DAYS, get_cached_fund_nav
from app.services.fund_sharpe import (
    SHARPE_SCHEMA_VERSION,
    compute_alipay_style_sharpes,
    compute_window_max_drawdown_percent,
)

_RISK_SOURCE = "computed_nav"
logger = logging.getLogger(__name__)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_risk_metrics_row(
    fund_code: str,
    points: list[Any] | None,
    *,
    as_of: object | None = None,
    available_at: str | None = None,
) -> dict[str, Any] | None:
    code = str(fund_code or "").zfill(6)
    series = list(points or [])
    if len(code) != 6 or not code.isdigit() or code == "000000" or len(series) < 2:
        return None
    payload = compute_alipay_style_sharpes(
        series,
        as_of=as_of,
        available_at=available_at,
    )
    one = payload["horizons"]["1y"]
    three = payload["horizons"]["3y"]
    drawdown = compute_window_max_drawdown_percent(series, years=1, as_of=as_of)
    if one is None and three is None and drawdown is None:
        return None
    return {
        "fund_code": code,
        "sharpe_1y": None if one is None else one["sharpe"],
        "sharpe_3y": None if three is None else three["sharpe"],
        "max_drawdown_1y_percent": drawdown,
        "nav_as_of": payload.get("as_of"),
        "nav_point_count": len(series),
        "schema_version": SHARPE_SCHEMA_VERSION,
        "snapshot_available_at": available_at or _now_utc_iso(),
        "source": _RISK_SOURCE,
        "sharpe_research": payload,
    }


def persist_risk_metrics_from_points(
    fund_code: str,
    points: list[Any] | None,
    *,
    as_of: object | None = None,
    available_at: str | None = None,
) -> dict[str, Any] | None:
    row = build_risk_metrics_row(
        fund_code,
        points,
        as_of=as_of,
        available_at=available_at,
    )
    if row is None:
        return None
    upsert_fund_risk_metrics(
        [row],
        snapshot_available_at=str(row["snapshot_available_at"]),
        source=_RISK_SOURCE,
        schema_version=SHARPE_SCHEMA_VERSION,
    )
    return row


def refresh_fund_risk_metrics_from_nav_cache(
    *,
    fund_codes: list[str] | None = None,
) -> int:
    """只读净值缓存，给已核验身份（或指定代码）补夏普/一年回撤。不拉源。"""

    codes = list(fund_codes or list_fresh_verified_identity_fund_codes())
    computed: list[dict[str, Any]] = []
    available_at = _now_utc_iso()
    for raw in codes:
        code = str(raw or "").zfill(6)
        history = get_cached_fund_nav(code, CANONICAL_NAV_TRADING_DAYS)
        points = list(getattr(history, "points", None) or [])
        row = build_risk_metrics_row(
            code,
            points,
            as_of=getattr(history, "latest_date", None),
            available_at=available_at,
        )
        if row is not None:
            computed.append(row)
    if not computed:
        return 0
    return upsert_fund_risk_metrics(
        computed,
        snapshot_available_at=available_at,
        source=_RISK_SOURCE,
        schema_version=SHARPE_SCHEMA_VERSION,
    )


def list_risk_metrics_for_codes(fund_codes: list[str] | set[str]) -> dict[str, dict]:
    return list_fund_risk_metrics_by_codes(fund_codes)


def list_all_risk_metrics() -> list[dict]:
    return list_fund_risk_metrics()


def apply_risk_metrics_to_row(row: dict, extra: dict | None) -> dict:
    if not extra:
        return row
    item = row
    for key in ("sharpe_1y", "sharpe_3y", "max_drawdown_1y_percent"):
        if item.get(key) is None and extra.get(key) is not None:
            item[key] = extra[key]
            stamp = extra.get("snapshot_available_at")
            if stamp:
                item.setdefault(f"{key}_available_at", stamp)
                item.setdefault(f"{key}_source", extra.get("source") or _RISK_SOURCE)
            as_of = extra.get("nav_as_of")
            if as_of:
                item.setdefault(f"{key}_as_of", as_of)
    return item
