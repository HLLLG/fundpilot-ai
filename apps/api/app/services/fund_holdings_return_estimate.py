"""主动基金当日涨跌：季报前十大加权估算（养基宝计算器口径）。

公式：``Σ(涨跌幅 × 净值占比) / 100``。不按已报价权重放大成 100%，
也不用关联板块代理主动/灵活配置基金的当日数字。

被动指数 / ETF / 联接 / LOF 仍跟跟踪指数；QDII 走既有估值链。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.models import FundProfile, Holding
from app.services.fund_primary_sector_service import _is_passive_index_fund_name
from app.services.fund_type_classification import has_positive_qdii_marker
from app.services.holding_estimates import (
    _amount_includes_today_return,
    compute_daily_profit_from_rate,
)

logger = logging.getLogger(__name__)

# 已报价权重 / 已披露权重；与 QDII ``compute_holdings_reference`` 对齐。
MIN_QUOTED_WEIGHT_RATIO = 0.35
_QUOTE_CACHE_PREFIX = "security-realtime-quote:v1:"
_QUOTE_CACHE_TTL_SECONDS = 60
_LOCKED_DAILY_SOURCES = frozenset({"official_nav", "pending_accrual"})


@dataclass(frozen=True, slots=True)
class HoldingsReturnEstimate:
    change_percent: float
    disclosed_weight_percent: float
    quoted_weight_percent: float
    quoted_count: int
    source: str = "holdings_estimate"


def should_use_holdings_weighted_daily(holding: Holding) -> bool:
    """主动 / 灵活配置等非跟踪指数基金，才用重仓加权估当日。"""

    code = (holding.fund_code or "").strip()
    if not code or code == "000000":
        return False
    name = holding.fund_name or ""
    if has_positive_qdii_marker(name):
        return False
    if _is_passive_index_fund_name(name):
        return False
    return True


def holding_row_secid(row: Mapping[str, Any]) -> str | None:
    """季报持仓行 → 东财 secid（A 股 ``0/1.xxxxxx``，港股 ``116.xxxxx``）。"""

    code = str(row.get("security_code") or "").strip()
    market = _market_for_row(row)
    if market == "CN" and len(code) == 6 and code.isdigit():
        eastmoney_market = "1" if code.startswith(("5", "6", "9")) else "0"
        return f"{eastmoney_market}.{code}"
    if market == "HK" and code.isdigit():
        digits = "".join(ch for ch in code if ch.isdigit()).zfill(5)[-5:]
        return f"116.{digits}" if digits else None
    return None


def compute_holdings_weighted_return(
    rows: Sequence[Mapping[str, Any]],
    quotes: Mapping[str, float],
    *,
    min_quoted_ratio: float = MIN_QUOTED_WEIGHT_RATIO,
) -> HoldingsReturnEstimate | None:
    """Compute disclosed-sleeve contribution; do not renormalize to 100% NAV."""

    if not rows or not quotes:
        return None
    if (
        isinstance(min_quoted_ratio, bool)
        or not isinstance(min_quoted_ratio, (int, float))
        or not math.isfinite(float(min_quoted_ratio))
        or not 0 <= float(min_quoted_ratio) <= 1
    ):
        return None

    disclosed_weight = 0.0
    quoted_weight = 0.0
    weighted_change = 0.0
    quoted_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        weight = _finite_positive(row.get("weight_percent"))
        if weight is None:
            continue
        disclosed_weight += weight
        secid = holding_row_secid(row)
        if not secid:
            continue
        change = _finite_number(quotes.get(secid))
        if change is None:
            continue
        quoted_weight += weight
        weighted_change += weight * change
        quoted_count += 1

    if disclosed_weight <= 0 or disclosed_weight > 100.01:
        return None
    if quoted_weight <= 0 or quoted_weight / disclosed_weight < float(min_quoted_ratio):
        return None
    return HoldingsReturnEstimate(
        change_percent=round(weighted_change / 100.0, 4),
        disclosed_weight_percent=round(disclosed_weight, 4),
        quoted_weight_percent=round(quoted_weight, 4),
        quoted_count=quoted_count,
    )


def estimate_holdings_weighted_returns(
    holdings: Sequence[Holding],
    *,
    allow_fetch: bool = True,
    allow_live_snapshot: bool = False,
) -> dict[str, HoldingsReturnEstimate]:
    """Batch-estimate eligible funds; stock quotes are deduped across the book."""

    candidates = [
        holding
        for holding in holdings
        if should_use_holdings_weighted_daily(holding)
    ]
    unique_codes = list(
        dict.fromkeys(
            (holding.fund_code or "").strip()
            for holding in candidates
            if (holding.fund_code or "").strip()
        )
    )
    if not unique_codes:
        return {}

    snapshots: dict[str, Mapping[str, Any]] = {}
    secids: list[str] = []
    for fund_code in unique_codes:
        snapshot = _load_qualified_snapshot(
            fund_code,
            allow_live=allow_live_snapshot,
        )
        if snapshot is None:
            continue
        snapshots[fund_code] = snapshot
        for row in snapshot.get("holdings") or []:
            if not isinstance(row, Mapping):
                continue
            secid = holding_row_secid(row)
            if secid:
                secids.append(secid)

    quotes = _load_quote_changes(secids, allow_fetch=allow_fetch)
    if not quotes:
        return {}

    result: dict[str, HoldingsReturnEstimate] = {}
    for fund_code, snapshot in snapshots.items():
        rows = snapshot.get("holdings")
        if not isinstance(rows, list):
            continue
        estimate = compute_holdings_weighted_return(rows, quotes)
        if estimate is not None:
            result[fund_code] = estimate
    return result


def apply_holdings_daily_estimates(
    holdings: Sequence[Holding],
    estimates: Mapping[str, HoldingsReturnEstimate],
    *,
    profiles: Sequence[FundProfile | None] | None = None,
) -> list[Holding]:
    """Write ``holdings_estimate`` unless official NAV / pending accrual wins."""

    if not estimates:
        return list(holdings)

    from app.services.profit_accrual_defer import is_profit_accrual_deferred

    updated: list[Holding] = []
    for index, holding in enumerate(holdings):
        if holding.daily_return_percent_source in _LOCKED_DAILY_SOURCES:
            updated.append(holding)
            continue
        profile = None
        if profiles is not None and index < len(profiles):
            profile = profiles[index]
        if is_profit_accrual_deferred(profile):
            updated.append(holding)
            continue
        estimate = estimates.get((holding.fund_code or "").strip())
        if estimate is None:
            updated.append(holding)
            continue
        amount = holding.settled_holding_amount or holding.holding_amount
        daily_profit = None
        if amount and amount > 0:
            daily_profit = compute_daily_profit_from_rate(
                amount,
                estimate.change_percent,
                amount_includes_today=_amount_includes_today_return(holding),
            )
        updated.append(
            holding.model_copy(
                update={
                    "daily_return_percent": estimate.change_percent,
                    "daily_profit": daily_profit,
                    "daily_return_percent_source": "holdings_estimate",
                }
            )
        )
    return updated


def _load_qualified_snapshot(
    fund_code: str,
    *,
    allow_live: bool,
) -> Mapping[str, Any] | None:
    from app.services.fund_holdings_snapshot_repository import (
        resolve_fund_holdings_snapshot_at_decision,
    )

    try:
        resolution = resolve_fund_holdings_snapshot_at_decision(
            fund_code,
            force_refresh=False,
            allow_live=False,
        )
    except Exception:
        logger.exception("holdings snapshot store read failed for %s", fund_code)
        resolution = None
    snapshot = _qualified_snapshot(resolution)
    if snapshot is not None:
        return snapshot
    if not allow_live:
        return None
    try:
        resolution = resolve_fund_holdings_snapshot_at_decision(
            fund_code,
            force_refresh=False,
            allow_live=True,
        )
    except Exception:
        logger.exception("holdings snapshot live resolve failed for %s", fund_code)
        return None
    return _qualified_snapshot(resolution)


def _qualified_snapshot(resolution: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(resolution, Mapping):
        return None
    snapshot = resolution.get("snapshot")
    if not isinstance(snapshot, Mapping) or snapshot.get("qualified") is not True:
        return None
    rows = snapshot.get("holdings")
    if not isinstance(rows, list) or not rows:
        return None
    return snapshot


def _load_quote_changes(
    secids: Sequence[str],
    *,
    allow_fetch: bool,
) -> dict[str, float]:
    requested = list(dict.fromkeys(str(secid).strip() for secid in secids if str(secid).strip()))
    if not requested:
        return {}

    from app.services.sector_quote_cache import (
        get_spot_snapshot,
        get_spot_snapshot_any_age,
        save_spot_snapshot,
    )

    changes: dict[str, float] = {}
    missing: list[str] = []
    for secid in requested:
        cached = get_spot_snapshot(
            f"{_QUOTE_CACHE_PREFIX}{secid}",
            ttl_seconds=_QUOTE_CACHE_TTL_SECONDS,
        )
        change = _quote_change(cached, secid)
        if change is not None:
            changes[secid] = change
        else:
            missing.append(secid)

    if missing and allow_fetch:
        from app.services.eastmoney_spot_client import fetch_eastmoney_quotes_by_secid

        try:
            fetched = fetch_eastmoney_quotes_by_secid(missing)
        except Exception:
            logger.exception("eastmoney holdings quote batch failed")
            fetched = {}
        for secid in missing:
            quote = fetched.get(secid) if isinstance(fetched, dict) else None
            change = _quote_change(quote, secid)
            if change is None:
                continue
            changes[secid] = change
            if isinstance(quote, dict):
                save_spot_snapshot(f"{_QUOTE_CACHE_PREFIX}{secid}", dict(quote))

    if len(changes) < len(requested):
        for secid in requested:
            if secid in changes:
                continue
            stale = get_spot_snapshot_any_age(f"{_QUOTE_CACHE_PREFIX}{secid}")
            change = _quote_change(stale, secid)
            if change is not None:
                changes[secid] = change
    return changes


def _market_for_row(row: Mapping[str, Any]) -> str | None:
    security_id = str(row.get("security_id") or "").strip().upper()
    if security_id.startswith("CN:"):
        return "CN"
    if security_id.startswith("HK:"):
        return "HK"
    explicit = str(row.get("security_market") or "").strip().upper()
    if explicit in {"CN", "HK"}:
        return explicit
    code = str(row.get("security_code") or "").strip()
    if len(code) == 6 and code.isdigit():
        return "CN"
    if len(code) == 5 and code.isdigit():
        return "HK"
    return None


def _quote_change(payload: object, secid: str) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    payload_secid = str(payload.get("secid") or "").strip()
    if payload_secid and payload_secid != secid:
        return None
    return _finite_number(payload.get("change_percent"))


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_positive(value: object) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


__all__ = [
    "HoldingsReturnEstimate",
    "MIN_QUOTED_WEIGHT_RATIO",
    "apply_holdings_daily_estimates",
    "compute_holdings_weighted_return",
    "estimate_holdings_weighted_returns",
    "holding_row_secid",
    "should_use_holdings_weighted_daily",
]
