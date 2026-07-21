"""Consumer-facing quarterly fund holdings distribution.

The decision pipeline keeps a strict, point-in-time holdings snapshot.  This
module reuses that validated disclosure for a read-only UI and adds one clearly
labelled presentation metric: a stock's share inside the fund's disclosed
stock allocation.  The latter is useful for feeder funds whose direct stock
book is small, but it must never be confused with the official fund-NAV weight.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.services.eastmoney_spot_client import fetch_eastmoney_quotes_by_secid
from app.services.fund_holdings_snapshot import (
    _DEFAULT_LIVE_HOLDINGS_SOURCE,
    _default_announcements_fetcher,
    _default_portfolio_rows_fetcher,
    resolve_fund_holdings_snapshot,
)
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)

CN_TZ = ZoneInfo("Asia/Shanghai")
_CACHE_PREFIX = "fund-holdings-distribution:v2:"
_CACHE_TTL_SECONDS = 6 * 60 * 60
_ALLOCATION_CACHE_PREFIX = "fund-stock-allocation:v1:"
_ALLOCATION_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_DANJUAN_ASSET_URL = (
    "https://danjuanfunds.com/djapi/fundx/base/fund/record/asset/percent"
)
_DANJUAN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}
_MAX_ROWS = 10
_ALLOCATION_ROUNDING_TOLERANCE_PERCENT = 2.0
_QUOTE_CACHE_PREFIX = "security-realtime-quote:v1:"
_QUOTE_CACHE_TTL_SECONDS = 60
_QUOTE_STALE_MAX_DAYS = 7


def build_fund_holdings_distribution(
    fund_code: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return the latest validated stock disclosure and prior-period changes."""

    code = _normalize_fund_code(fund_code)
    cache_key = f"{_CACHE_PREFIX}{code}"
    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=_CACHE_TTL_SECONDS)
        if _valid_cached_payload(cached, code):
            return _attach_market_quotes(dict(cached), force_refresh=False)

    decision_at = datetime.now(CN_TZ)
    current, previous = _resolve_snapshot_pair(code, decision_at=decision_at)
    if not _qualified_snapshot(current):
        return _unavailable_payload(
            code,
            reason_codes=list(current.get("reason_codes") or []) if current else [],
        )

    current_holdings = _holding_rows(current)
    if not current_holdings:
        return _unavailable_payload(code, reason_codes=["disclosed_holdings_missing"])

    report_dates = [str(current.get("as_of_date") or "")]
    if _qualified_snapshot(previous):
        report_dates.append(str(previous.get("as_of_date") or ""))
    allocations = _fetch_stock_allocations(
        code,
        report_dates,
        force_refresh=force_refresh,
    )

    current_allocation = _usable_stock_allocation(current, allocations)
    previous_allocation = (
        _usable_stock_allocation(previous, allocations)
        if _qualified_snapshot(previous)
        else None
    )
    previous_by_code = {
        str(row.get("security_code") or "").strip(): row
        for row in _holding_rows(previous)
    }
    display_basis = "stock_position" if current_allocation is not None else "fund_nav"

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(current_holdings[:_MAX_ROWS], start=1):
        security_code = str(row.get("security_code") or "").strip()
        nav_weight = _number(row.get("weight_percent"))
        if nav_weight is None:
            continue
        display_weight = _display_weight(nav_weight, current_allocation)
        previous_row = previous_by_code.get(security_code)
        previous_nav_weight = (
            _number(previous_row.get("weight_percent"))
            if isinstance(previous_row, Mapping)
            else None
        )
        previous_display_weight = (
            _display_weight(previous_nav_weight, previous_allocation)
            if previous_nav_weight is not None
            else None
        )
        comparison_basis = (
            "stock_position"
            if current_allocation is not None
            and previous_allocation is not None
            and previous_display_weight is not None
            else "fund_nav"
        )
        current_comparison_weight = (
            display_weight if comparison_basis == "stock_position" else nav_weight
        )
        previous_comparison_weight = (
            previous_display_weight
            if comparison_basis == "stock_position"
            else previous_nav_weight
        )
        change, direction = _weight_change(
            current_comparison_weight,
            previous_comparison_weight,
        )
        rows.append(
            {
                "rank": _positive_int(row.get("rank")) or index,
                "security_code": security_code,
                "security_name": str(row.get("security_name") or "").strip(),
                "security_market": _security_market(row.get("security_id")),
                "nav_weight_percent": round(nav_weight, 2),
                "display_weight_percent": round(display_weight, 2),
                "display_weight_basis": display_basis,
                "previous_nav_weight_percent": (
                    round(previous_nav_weight, 2)
                    if previous_nav_weight is not None
                    else None
                ),
                "previous_display_weight_percent": (
                    round(previous_display_weight, 2)
                    if previous_display_weight is not None
                    else None
                ),
                "change_percent_points": change,
                "change_direction": direction,
                "comparison_basis": comparison_basis,
            }
        )

    coverage = current.get("coverage")
    coverage_map = coverage if isinstance(coverage, Mapping) else {}
    payload = {
        "fund_code": code,
        "status": "available",
        "report_period": current.get("report_period"),
        "as_of_date": current.get("as_of_date"),
        "disclosed_at": current.get("available_at"),
        "freshness": _freshness_label(current),
        "previous_report_period": (
            previous.get("report_period") if _qualified_snapshot(previous) else None
        ),
        "previous_as_of_date": (
            previous.get("as_of_date") if _qualified_snapshot(previous) else None
        ),
        "display_weight_basis": display_basis,
        "stock_allocation_percent": (
            round(current_allocation, 2) if current_allocation is not None else None
        ),
        "disclosed_weight_percent": _rounded_optional(
            coverage_map.get("portfolio_weight_coverage_percent")
            or coverage_map.get("weight_sum_percent")
        ),
        "holdings": rows,
        "source": "eastmoney_quarterly_disclosure",
        "allocation_source": (
            "xueqiu_fund_archive" if current_allocation is not None else None
        ),
        "data_note": (
            "股票仓位内占比按季报股票仓位归一化；占基金净值为官方披露口径。"
            if display_basis == "stock_position"
            else "持仓比例为季报披露的占基金净值比例。"
        ),
        "generated_at": decision_at.isoformat(),
        "reason_codes": [],
    }
    save_spot_snapshot(cache_key, payload)
    return _attach_market_quotes(payload, force_refresh=force_refresh)


def _resolve_snapshot_pair(
    fund_code: str,
    *,
    decision_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Fetch disclosure inputs once, then resolve current and prior snapshots."""

    rows_cache: dict[str, object] = {}
    announcements_cache: dict[str, object] = {}

    def rows_provider(
        code: str,
        *,
        years: Sequence[str],
        decision_at: datetime,
    ) -> object:
        del decision_at
        requested = sorted({str(year) for year in years})
        cached_years = rows_cache.get("years")
        if rows_cache.get("value") is None or cached_years != requested:
            rows_cache["years"] = requested
            rows_cache["value"] = _default_portfolio_rows_fetcher(
                code,
                years=requested,
                decision_at=decision_at_outer,
            )
        return rows_cache["value"]

    def announcements_provider(
        code: str,
        *,
        limit: int,
        decision_at: datetime,
    ) -> object:
        del decision_at
        if announcements_cache.get("value") is None:
            announcements_cache["value"] = _default_announcements_fetcher(
                code,
                limit=max(limit, 100),
                decision_at=decision_at_outer,
            )
        return announcements_cache["value"]

    decision_at_outer = decision_at
    current = resolve_fund_holdings_snapshot(
        fund_code,
        decision_at=decision_at,
        fetch_portfolio_rows=rows_provider,
        fetch_announcements=announcements_provider,
        source=_DEFAULT_LIVE_HOLDINGS_SOURCE,
    )
    if not _qualified_snapshot(current):
        return current, None

    disclosed_at = _aware_datetime(current.get("available_at"))
    if disclosed_at is None:
        return current, None
    prior_decision = disclosed_at - timedelta(microseconds=1)
    previous = resolve_fund_holdings_snapshot(
        fund_code,
        decision_at=prior_decision,
        fetch_portfolio_rows=rows_provider,
        fetch_announcements=announcements_provider,
        source=_DEFAULT_LIVE_HOLDINGS_SOURCE,
    )
    if (
        not _qualified_snapshot(previous)
        or previous.get("report_period") == current.get("report_period")
    ):
        previous = None
    return current, previous


def _fetch_stock_allocations(
    fund_code: str,
    report_dates: Sequence[str],
    *,
    force_refresh: bool = False,
) -> dict[str, float]:
    dates = sorted(
        {
            str(value).replace("-", "")
            for value in report_dates
            if len(str(value).replace("-", "")) == 8
            and str(value).replace("-", "").isdigit()
        }
    )
    if not dates:
        return {}
    result: dict[str, float] = {}
    missing: list[str] = []
    for report_date in dates:
        cache_key = f"{_ALLOCATION_CACHE_PREFIX}{fund_code}:{report_date}"
        cached = (
            None
            if force_refresh
            else get_spot_snapshot(
                cache_key,
                ttl_seconds=_ALLOCATION_CACHE_TTL_SECONDS,
            )
        )
        number = _number(
            cached.get("stock_allocation_percent")
            if isinstance(cached, Mapping)
            else None
        )
        if number is not None and 0 < number <= 100:
            result[report_date] = number
        else:
            missing.append(report_date)

    if missing:
        with httpx.Client(
            headers=_DANJUAN_HEADERS,
            timeout=15.0,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            for report_date in missing:
                cache_key = f"{_ALLOCATION_CACHE_PREFIX}{fund_code}:{report_date}"
                number: float | None = None
                try:
                    iso_date = (
                        f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:]}"
                    )
                    response = client.get(
                        _DANJUAN_ASSET_URL,
                        params={"fund_code": fund_code, "report_date": iso_date},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    data = payload.get("data") if isinstance(payload, Mapping) else None
                    if isinstance(data, Mapping):
                        number = _number(data.get("stock_percent"))
                        if number is None:
                            chart = data.get("chart_list")
                            if isinstance(chart, Sequence) and not isinstance(
                                chart, (str, bytes)
                            ):
                                for row in chart:
                                    if not isinstance(row, Mapping):
                                        continue
                                    asset_type = str(
                                        row.get("type_desc")
                                        or row.get("资产类型")
                                        or ""
                                    ).strip()
                                    if asset_type == "股票":
                                        number = _number(
                                            row.get("percent") or row.get("仓位占比")
                                        )
                                        break
                except Exception:
                    number = None

                if number is not None and 0 < number <= 100:
                    result[report_date] = number
                    save_spot_snapshot(
                        cache_key,
                        {
                            "fund_code": fund_code,
                            "report_date": report_date,
                            "stock_allocation_percent": number,
                            "source": "danjuan_fund_archive",
                        },
                    )
                    continue

                stale = get_spot_snapshot_any_age(cache_key)
                stale_number = _number(
                    stale.get("stock_allocation_percent")
                    if isinstance(stale, Mapping)
                    else None
                )
                if stale_number is not None and 0 < stale_number <= 100:
                    result[report_date] = stale_number
    return result


def _attach_market_quotes(
    payload: Mapping[str, Any],
    *,
    force_refresh: bool,
) -> dict[str, Any]:
    """Join a short-lived quote snapshot onto the long-lived disclosure cache."""

    enriched = dict(payload)
    raw_holdings = payload.get("holdings")
    if (
        payload.get("status") != "available"
        or not isinstance(raw_holdings, list)
        or not raw_holdings
    ):
        enriched.setdefault("quote_session_date", None)
        enriched.setdefault("quote_updated_at", None)
        enriched.setdefault("quote_source", None)
        return enriched

    holdings = [dict(row) for row in raw_holdings if isinstance(row, Mapping)]
    row_secids = {
        str(row.get("security_code") or ""): _quote_secid(row)
        for row in holdings
    }
    quotes = _fetch_stock_quotes(
        [secid for secid in row_secids.values() if secid],
        force_refresh=force_refresh,
    )
    quote_times: list[datetime] = []
    resolved_quotes: list[
        tuple[dict[str, Any], float | None, datetime | None]
    ] = []
    for row in holdings:
        code = str(row.get("security_code") or "")
        secid = row_secids.get(code)
        quote = quotes.get(secid) if secid else None
        change = _number(quote.get("change_percent")) if quote else None
        quote_time = _quote_datetime(
            quote.get("quote_timestamp") if quote else None
        )
        if quote_time is not None:
            quote_times.append(quote_time)
        resolved_quotes.append((row, change, quote_time))

    latest_quote = max(quote_times) if quote_times else None
    latest_session = latest_quote.date() if latest_quote is not None else None
    for row, change, quote_time in resolved_quotes:
        same_session = bool(
            latest_session is not None
            and quote_time is not None
            and quote_time.date() == latest_session
        )
        row["quote_change_percent"] = (
            round(change, 2)
            if same_session and change is not None and -100 <= change <= 1000
            else None
        )
    enriched["holdings"] = holdings
    enriched["quote_session_date"] = (
        latest_quote.date().isoformat() if latest_quote is not None else None
    )
    enriched["quote_updated_at"] = (
        latest_quote.isoformat() if latest_quote is not None else None
    )
    enriched["quote_source"] = (
        "eastmoney_realtime_quote"
        if any(row.get("quote_change_percent") is not None for row in holdings)
        else None
    )
    return enriched


def _fetch_stock_quotes(
    secids: Sequence[str],
    *,
    force_refresh: bool,
) -> dict[str, dict[str, Any]]:
    requested = list(dict.fromkeys(str(value) for value in secids if value))
    if not requested:
        return {}

    result: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for secid in requested:
        cache_key = f"{_QUOTE_CACHE_PREFIX}{secid}"
        cached = (
            None
            if force_refresh
            else get_spot_snapshot(cache_key, ttl_seconds=_QUOTE_CACHE_TTL_SECONDS)
        )
        if _valid_quote_payload(cached, secid):
            result[secid] = dict(cached)
        else:
            missing.append(secid)

    fetched: dict[str, dict[str, Any]] = {}
    if missing:
        try:
            fetched = fetch_eastmoney_quotes_by_secid(missing)
        except Exception:
            fetched = {}

    for secid in missing:
        cache_key = f"{_QUOTE_CACHE_PREFIX}{secid}"
        quote = fetched.get(secid)
        if _valid_quote_payload(quote, secid):
            normalized = dict(quote)
            result[secid] = normalized
            save_spot_snapshot(cache_key, normalized)
            continue

        stale = get_spot_snapshot_any_age(cache_key)
        if _valid_quote_payload(stale, secid) and _quote_is_recent(stale):
            result[secid] = dict(stale)
    return result


def _quote_secid(row: Mapping[str, Any]) -> str | None:
    code = str(row.get("security_code") or "").strip()
    market = str(row.get("security_market") or "").strip().upper()
    if market == "CN" and len(code) == 6 and code.isdigit():
        eastmoney_market = "1" if code.startswith(("5", "6", "9")) else "0"
        return f"{eastmoney_market}.{code}"
    if market == "HK" and len(code) == 5 and code.isdigit():
        return f"116.{code}"
    return None


def _security_market(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if text.startswith("CN:"):
        return "CN"
    if text.startswith("HK:"):
        return "HK"
    return None


def _valid_quote_payload(value: object, secid: str) -> bool:
    if not isinstance(value, Mapping) or value.get("secid") != secid:
        return False
    change = value.get("change_percent")
    if change is not None and _number(change) is None:
        return False
    return _quote_datetime(value.get("quote_timestamp")) is not None


def _quote_datetime(value: object) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number, tz=CN_TZ)
    except (OSError, OverflowError, ValueError):
        return None


def _quote_is_recent(value: Mapping[str, Any]) -> bool:
    quoted_at = _quote_datetime(value.get("quote_timestamp"))
    if quoted_at is None:
        return False
    age = datetime.now(CN_TZ).date() - quoted_at.date()
    return 0 <= age.days <= _QUOTE_STALE_MAX_DAYS


def _usable_stock_allocation(
    snapshot: Mapping[str, Any] | None,
    allocations: Mapping[str, float],
) -> float | None:
    if not _qualified_snapshot(snapshot):
        return None
    key = str(snapshot.get("as_of_date") or "").replace("-", "")
    allocation = _number(allocations.get(key))
    if allocation is None or not 0 < allocation <= 100:
        return None
    disclosed = sum(
        weight
        for row in _holding_rows(snapshot)
        if (weight := _number(row.get("weight_percent"))) is not None
    )
    if disclosed > allocation + _ALLOCATION_ROUNDING_TOLERANCE_PERCENT:
        return None
    return allocation


def _holding_rows(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, Mapping):
        return []
    raw = snapshot.get("holdings")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    rows = [dict(row) for row in raw if isinstance(row, Mapping)]
    rows.sort(
        key=lambda row: (
            -(_number(row.get("weight_percent")) or 0.0),
            _positive_int(row.get("rank")) or 10**9,
            str(row.get("security_code") or ""),
        )
    )
    return rows


def _display_weight(nav_weight: float, stock_allocation: float | None) -> float:
    if stock_allocation is None:
        return nav_weight
    return nav_weight / stock_allocation * 100.0


def _weight_change(
    current: float,
    previous: float | None,
) -> tuple[float | None, str]:
    if previous is None:
        return None, "new"
    change = round(current - previous, 2)
    if change > 0.004:
        return change, "increased"
    if change < -0.004:
        return change, "decreased"
    return 0.0, "unchanged"


def _qualified_snapshot(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("status") == "qualified"
        and value.get("qualified") is True
    )


def _freshness_label(snapshot: Mapping[str, Any]) -> str:
    value = snapshot.get("freshness")
    if isinstance(value, Mapping):
        label = str(value.get("label") or "unknown")
        if label in {"fresh", "aging", "stale", "unknown"}:
            return label
    return "unknown"


def _unavailable_payload(
    fund_code: str,
    *,
    reason_codes: list[str],
) -> dict[str, Any]:
    return {
        "fund_code": fund_code,
        "status": "unavailable",
        "report_period": None,
        "as_of_date": None,
        "disclosed_at": None,
        "freshness": "unknown",
        "previous_report_period": None,
        "previous_as_of_date": None,
        "display_weight_basis": "fund_nav",
        "stock_allocation_percent": None,
        "disclosed_weight_percent": None,
        "holdings": [],
        "source": "eastmoney_quarterly_disclosure",
        "allocation_source": None,
        "quote_session_date": None,
        "quote_updated_at": None,
        "quote_source": None,
        "data_note": "暂未取得可核验的季度股票持仓。",
        "generated_at": datetime.now(CN_TZ).isoformat(),
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def _valid_cached_payload(value: object, fund_code: str) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("fund_code") == fund_code
        and value.get("status") in {"available", "unavailable"}
        and isinstance(value.get("holdings"), list)
    )


def _normalize_fund_code(value: object) -> str:
    code = str(value or "").strip()
    if not code.isdigit() or len(code) > 6:
        raise ValueError("基金代码须为最多六位数字")
    return code.zfill(6)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _rounded_optional(value: object) -> float | None:
    number = _number(value)
    return round(number, 2) if number is not None else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


__all__ = ["build_fund_holdings_distribution"]
