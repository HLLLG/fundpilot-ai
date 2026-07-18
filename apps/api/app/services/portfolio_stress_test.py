"""Fail-closed historical replay stress test for the current portfolio.

The result is a diagnostic artifact only.  It uses today's portfolio weights
against aligned historical NAV returns, does not forecast future losses, and
cannot change holdings or allocation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import json
import math
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "portfolio_stress_test.v1"
MODEL_VERSION = "current_weight_historical_replay.v1"
MODE = "shadow_diagnostic_only"
MINIMUM_COMMON_RETURN_DAYS = 60
MAX_HOLDINGS = 15


def build_portfolio_stress_test(
    holdings: Sequence[Any],
    *,
    lookback_days: int = 252,
    fetch_history: Callable[[str, str, int], Any] | None = None,
    now: datetime | None = None,
    minimum_common_return_days: int = MINIMUM_COMMON_RETURN_DAYS,
    max_holdings: int = MAX_HOLDINGS,
) -> dict[str, Any]:
    """Replay current fixed weights over common historical NAV return dates."""

    current = _aware_now(now)
    generated_at = current.isoformat()
    cutoff_date = current.astimezone(ZoneInfo("Asia/Shanghai")).date()
    normalized, reasons = _normalize_holdings(holdings)
    if not normalized:
        reasons.append("positive_current_holdings_unavailable")
    if len(normalized) > max_holdings:
        reasons.append("holding_count_exceeds_bounded_fetch_limit")
    if reasons:
        return _finish_unavailable(
            generated_at=generated_at,
            lookback_days=lookback_days,
            holdings=normalized,
            reason_codes=reasons,
        )

    bounded_lookback = max(minimum_common_return_days, min(int(lookback_days), 400))
    loader = fetch_history or _default_fetch_history

    def load(row: Mapping[str, Any]) -> tuple[str, str | None, dict[str, float]]:
        code = str(row["fund_code"])
        try:
            history = loader(code, str(row.get("fund_name") or ""), bounded_lookback + 1)
        except Exception:
            return code, None, {}
        source = _field(history, "source")
        points = _field(history, "points")
        return (
            code,
            str(source or "").strip() or None,
            _nav_by_date(points, cutoff_date=cutoff_date),
        )

    histories: dict[str, tuple[str | None, dict[str, float]]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(normalized))) as pool:
        for code, source, returns in pool.map(load, normalized):
            histories[code] = (source, returns)

    source_rows: list[dict[str, Any]] = []
    missing_codes: list[str] = []
    for row in normalized:
        code = str(row["fund_code"])
        source, nav_by_date = histories.get(code, (None, {}))
        usable = bool(
            source
            and source not in {"unavailable", "error", "manual"}
            and len(nav_by_date) >= 2
        )
        source_rows.append(
            {
                "fund_code": code,
                "source": source,
                "return_day_count": max(0, len(nav_by_date) - 1),
                "usable": usable,
            }
        )
        if not usable:
            missing_codes.append(code)
    if missing_codes:
        return _finish_unavailable(
            generated_at=generated_at,
            lookback_days=bounded_lookback,
            holdings=normalized,
            reason_codes=["holding_nav_history_incomplete"],
            sources=source_rows,
            missing_fund_codes=missing_codes,
        )

    common_dates: set[str] | None = None
    for row in normalized:
        nav_by_date = histories[str(row["fund_code"])][1]
        common_dates = (
            set(nav_by_date)
            if common_dates is None
            else common_dates & set(nav_by_date)
        )
    common_nav_dates = sorted(common_dates or set())[-(bounded_lookback + 1):]
    common_return_days = max(0, len(common_nav_dates) - 1)
    if common_return_days < minimum_common_return_days:
        return _finish_unavailable(
            generated_at=generated_at,
            lookback_days=bounded_lookback,
            holdings=normalized,
            reason_codes=["common_return_sample_insufficient"],
            sources=source_rows,
            common_return_days=common_return_days,
        )

    total_amount = sum(float(row["holding_amount_yuan"]) for row in normalized)
    weights = {
        str(row["fund_code"]): float(row["holding_amount_yuan"]) / total_amount
        for row in normalized
    }
    portfolio_returns: list[float] = []
    for index in range(1, len(common_nav_dates)):
        previous_day = common_nav_dates[index - 1]
        current_day = common_nav_dates[index]
        portfolio_returns.append(
            sum(
                weights[str(row["fund_code"])]
                * (
                    histories[str(row["fund_code"])][1][current_day]
                    / histories[str(row["fund_code"])][1][previous_day]
                    - 1.0
                )
                for row in normalized
            )
        )
    dates = common_nav_dates[1:]

    scenarios = [
        _worst_window_scenario(
            "worst_observed_1d",
            "历史最差单日",
            common_nav_dates,
            portfolio_returns,
            window=1,
            total_amount=total_amount,
        ),
        _worst_window_scenario(
            "worst_observed_5d",
            "历史最差连续 5 个交易日",
            common_nav_dates,
            portfolio_returns,
            window=5,
            total_amount=total_amount,
        ),
        _worst_window_scenario(
            "worst_observed_20d",
            "历史最差连续 20 个交易日",
            common_nav_dates,
            portfolio_returns,
            window=20,
            total_amount=total_amount,
        ),
        _expected_shortfall_scenario(
            dates,
            portfolio_returns,
            total_amount=total_amount,
        ),
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "mode": MODE,
        "generated_at": generated_at,
        "status": "available",
        "available": True,
        "selection_effect": "none",
        "allocation_effect": "none",
        "automatic_action_allowed": False,
        "forecast": False,
        "interpretation": "historical_replay_not_loss_forecast",
        "weight_policy": "current_market_value_constant_weight_daily_rebalanced_proxy",
        "missing_value_policy": "all_holdings_required_no_imputation_no_weight_renormalization",
        "lookback_days": bounded_lookback,
        "sample": {
            "common_return_days": common_return_days,
            "start_date": common_nav_dates[0],
            "end_date": common_nav_dates[-1],
            "holding_count": len(normalized),
            "total_current_holding_amount_yuan": round(total_amount, 2),
        },
        "holdings": [
            {
                **row,
                "current_weight_percent": round(
                    weights[str(row["fund_code"])] * 100.0,
                    6,
                ),
            }
            for row in normalized
        ],
        "sources": source_rows,
        "scenarios": scenarios,
        "reason_codes": [],
        "notices": [
            "仅重放当前权重在历史共同净值区间的表现，不预测未来。",
            "缺少任一持仓净值或共同样本不足时整包失败关闭，不补值、不重分权重。",
            "结果只供人工风险复核，不会触发自动调仓或改变荐基排序。",
        ],
        "hash_algorithm": "sha256",
        "canonicalization": "json_utf8_sort_keys_v1",
    }
    return _seal(payload)


def validate_portfolio_stress_test(payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if payload.get("model_version") != MODEL_VERSION:
        errors.append("model_version_invalid")
    if payload.get("mode") != MODE:
        errors.append("mode_invalid")
    if payload.get("automatic_action_allowed") is not False:
        errors.append("automatic_action_must_be_false")
    if payload.get("forecast") is not False:
        errors.append("forecast_flag_must_be_false")
    supplied = payload.get("snapshot_hash")
    if supplied != _hash_payload(_hash_material(payload)):
        errors.append("snapshot_hash_invalid")
    if payload.get("available") is True:
        scenarios = payload.get("scenarios")
        if not isinstance(scenarios, list) or len(scenarios) != 4:
            errors.append("scenario_set_invalid")
        sample = payload.get("sample") if isinstance(payload.get("sample"), Mapping) else {}
        if _positive_int(sample.get("common_return_days")) < MINIMUM_COMMON_RETURN_DAYS:
            errors.append("common_return_sample_invalid")
    return {
        "status": "valid" if not errors else "invalid",
        "error_codes": sorted(set(errors)),
    }


def _finish_unavailable(
    *,
    generated_at: str,
    lookback_days: int,
    holdings: Sequence[Mapping[str, Any]],
    reason_codes: Sequence[str],
    sources: Sequence[Mapping[str, Any]] = (),
    missing_fund_codes: Sequence[str] = (),
    common_return_days: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "mode": MODE,
        "generated_at": generated_at,
        "status": "insufficient_evidence",
        "available": False,
        "selection_effect": "none",
        "allocation_effect": "none",
        "automatic_action_allowed": False,
        "forecast": False,
        "interpretation": "historical_replay_not_loss_forecast",
        "weight_policy": "current_market_value_constant_weight_daily_rebalanced_proxy",
        "missing_value_policy": "all_holdings_required_no_imputation_no_weight_renormalization",
        "lookback_days": max(0, int(lookback_days)),
        "sample": {
            "common_return_days": max(0, int(common_return_days)),
            "start_date": None,
            "end_date": None,
            "holding_count": len(holdings),
            "total_current_holding_amount_yuan": round(
                sum(float(row.get("holding_amount_yuan") or 0) for row in holdings),
                2,
            ),
        },
        "holdings": [dict(row) for row in holdings],
        "sources": [dict(row) for row in sources],
        "scenarios": [],
        "reason_codes": sorted(set(str(value) for value in reason_codes if value)),
        "missing_fund_codes": sorted(set(missing_fund_codes)),
        "notices": [
            "证据不足时不生成压力数字；空值不是零风险。",
            "结果只供人工风险复核，不会触发自动调仓或改变荐基排序。",
        ],
        "hash_algorithm": "sha256",
        "canonicalization": "json_utf8_sort_keys_v1",
    }
    return _seal(payload)


def _normalize_holdings(holdings: Sequence[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    seen: set[str] = set()
    for item in holdings:
        amount = _finite_number(_field(item, "holding_amount"))
        if amount is None or amount <= 0:
            continue
        code = _fund_code(_field(item, "fund_code"))
        if code is None:
            reasons.append("holding_fund_code_invalid")
            continue
        if code in seen:
            reasons.append("holding_fund_code_duplicated")
            continue
        seen.add(code)
        rows.append(
            {
                "fund_code": code,
                "fund_name": str(_field(item, "fund_name") or "").strip() or code,
                "holding_amount_yuan": round(amount, 2),
            }
        )
    rows.sort(key=lambda row: str(row["fund_code"]))
    return rows, sorted(set(reasons))


def _nav_by_date(points: Any, *, cutoff_date: date) -> dict[str, float]:
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
        return {}
    normalized: list[tuple[str, float]] = []
    seen: set[str] = set()
    for point in points:
        day = str(_field(point, "date") or "")[:10]
        nav = _finite_number(_field(point, "nav"))
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            return {}
        if parsed_day > cutoff_date or nav is None or nav <= 0 or day in seen:
            return {}
        seen.add(day)
        normalized.append((day, nav))
    normalized.sort(key=lambda item: item[0])
    return dict(normalized)


def _worst_window_scenario(
    scenario_id: str,
    label: str,
    nav_dates: Sequence[str],
    returns: Sequence[float],
    *,
    window: int,
    total_amount: float,
) -> dict[str, Any]:
    candidates: list[tuple[float, int]] = []
    for start in range(0, len(returns) - window + 1):
        compounded = 1.0
        for value in returns[start : start + window]:
            compounded *= 1.0 + value
        candidates.append((compounded - 1.0, start))
    result, start = min(candidates, key=lambda item: (item[0], item[1]))
    percent = result * 100.0
    return {
        "scenario_id": scenario_id,
        "label": label,
        "method": "worst_observed_rolling_compound_return",
        "window_trading_days": window,
        "return_percent": round(percent, 6),
        "estimated_loss_yuan": round(max(0.0, -result * total_amount), 2),
        "start_date": nav_dates[start],
        "end_date": nav_dates[start + window],
        "forecast": False,
    }


def _expected_shortfall_scenario(
    dates: Sequence[str],
    returns: Sequence[float],
    *,
    total_amount: float,
) -> dict[str, Any]:
    tail_count = max(1, math.ceil(len(returns) * 0.05))
    tail = sorted(zip(returns, dates, strict=True), key=lambda item: (item[0], item[1]))[
        :tail_count
    ]
    expected = sum(item[0] for item in tail) / len(tail)
    return {
        "scenario_id": "historical_expected_shortfall_95_1d",
        "label": "历史 95% 单日期望损失",
        "method": "mean_of_worst_five_percent_observed_daily_returns",
        "window_trading_days": 1,
        "return_percent": round(expected * 100.0, 6),
        "estimated_loss_yuan": round(max(0.0, -expected * total_amount), 2),
        "start_date": min(item[1] for item in tail),
        "end_date": max(item[1] for item in tail),
        "tail_observation_count": tail_count,
        "forecast": False,
    }


def _default_fetch_history(code: str, name: str, trading_days: int) -> Any:
    from app.services.fund_data import FundDataService

    return FundDataService().get_nav_history(code, name, trading_days=trading_days)


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["snapshot_hash"] = _hash_payload(_hash_material(payload))
    payload["validation"] = validate_portfolio_stress_test(payload)
    return payload


def _hash_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(payload)
    material.pop("snapshot_hash", None)
    material.pop("validation", None)
    return material


def _hash_payload(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _field(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _fund_code(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 6:
        return None
    code = text.zfill(6)
    return code if code != "000000" else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return parsed if parsed > 0 else 0


def _aware_now(value: datetime | None) -> datetime:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return moment


__all__ = [
    "MAX_HOLDINGS",
    "MINIMUM_COMMON_RETURN_DAYS",
    "MODEL_VERSION",
    "SCHEMA_VERSION",
    "build_portfolio_stress_test",
    "validate_portfolio_stress_test",
]
