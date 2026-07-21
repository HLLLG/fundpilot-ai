"""Deterministic sector-mainline research model.

The model is deliberately descriptive and ranking-only.  It may change which
sectors are researched first, but it never authorizes a trade, changes an
allocator limit, or bypasses the existing quality/tradeability/risk guards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from app.services.decision_repository import canonical_hash
from app.services.trading_session import build_trading_session


MAINLINE_REGIME_SCHEMA_VERSION = "mainline_regime.v1"
MAINLINE_SNAPSHOT_SCHEMA_VERSION = "mainline_daily_snapshot.v1"
MAINLINE_POLICY_VERSION = "mainline_research_ranking.2026-07.v1"

_COMPONENT_WEIGHTS = {
    "relative_strength": 0.35,
    "trend_persistence": 0.25,
    "fund_flow": 0.20,
    "breadth": 0.10,
    "market_structure": 0.10,
}


def build_mainline_regime_snapshot(
    sector_heat: Sequence[Mapping[str, Any]],
    *,
    sector_flow_by_label: Mapping[str, Mapping[str, Any]] | None = None,
    sector_position_by_label: Mapping[str, Mapping[str, Any]] | None = None,
    sector_labels: Sequence[str] | None = None,
    decision_at: datetime | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Build one compact, point-in-time mainline snapshot.

    Only rows whose providers have already been bounded to the effective trade
    date should be supplied.  The snapshot records coverage explicitly and
    fails closed to ``insufficient`` when the 20-day price structure or enough
    independent feature families are unavailable.
    """

    session = build_trading_session(decision_at)
    trade_date = str(session.get("effective_trade_date") or "").strip() or None
    decision_clock = _aware_utc(decision_at or datetime.now(timezone.utc))
    capture_clock = _aware_utc(captured_at or datetime.now(timezone.utc))
    if capture_clock < decision_clock:
        capture_clock = decision_clock

    heat_by_label = _rows_by_label(sector_heat)
    flow_by_label = {
        str(label).strip(): dict(row)
        for label, row in (sector_flow_by_label or {}).items()
        if str(label).strip() and isinstance(row, Mapping)
    }
    position_by_label = {
        str(label).strip(): dict(row)
        for label, row in (sector_position_by_label or {}).items()
        if str(label).strip() and isinstance(row, Mapping)
    }
    labels = _ordered_labels(
        sector_labels,
        heat_by_label,
        flow_by_label,
        position_by_label,
    )

    percentile_inputs = _build_percentile_inputs(labels, position_by_label, flow_by_label)
    regimes = [
        _build_regime(
            label,
            heat=heat_by_label.get(label) or {},
            flow=flow_by_label.get(label) or {},
            position=position_by_label.get(label) or {},
            percentiles=percentile_inputs,
            session_kind=str(session.get("session_kind") or ""),
            trade_date=trade_date,
        )
        for label in labels
    ]
    regimes.sort(
        key=lambda row: (
            _status_priority(str(row.get("status") or "")),
            _number(row.get("score")) or -1.0,
            str(row.get("sector_label") or ""),
        ),
        reverse=True,
    )
    snapshot: dict[str, Any] = {
        "schema_version": MAINLINE_SNAPSHOT_SCHEMA_VERSION,
        "policy_version": MAINLINE_POLICY_VERSION,
        "decision_at": decision_clock.isoformat(),
        "captured_at": capture_clock.isoformat(),
        "effective_trade_date": trade_date,
        "session_kind": session.get("session_kind"),
        "decision_policy": "research_ranking_only",
        "entry_policy_version": "sector_entry_maturity.2026-07.v2",
        "execution_gate_changed": False,
        "automatic_promotion_allowed": False,
        "benchmark": _benchmark_summary(regimes),
        "sector_count": len(regimes),
        "available_count": sum(row.get("status") != "insufficient" for row in regimes),
        "ranking": [str(row["sector_label"]) for row in regimes],
        "sectors": regimes,
    }
    snapshot["snapshot_hash"] = canonical_hash(snapshot)
    return snapshot


def mainline_regime_by_label(snapshot: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in (snapshot or {}).get("sectors") or []:
        if not isinstance(row, Mapping):
            continue
        label = str(row.get("sector_label") or "").strip()
        if label:
            result[label] = dict(row)
    return result


def align_sector_opportunities_with_mainline_snapshot(
    opportunities: Sequence[Mapping[str, Any]] | None,
    snapshot: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Make the frozen snapshot authoritative for every direction card.

    Streaming and persisted reports may be assembled in separate steps.  A
    previously calculated ``mainline_regime`` nested in an opportunity must
    never override the same decision's frozen snapshot, otherwise the UI can
    show missing 20-day evidence while the snapshot already contains it.
    """

    by_label = mainline_regime_by_label(snapshot)
    aligned: list[dict[str, Any]] = []
    for raw in opportunities or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        label = str(item.get("sector_label") or "").strip()
        if label in by_label:
            item["mainline_regime"] = dict(by_label[label])
        aligned.append(item)
    return aligned


def _build_regime(
    label: str,
    *,
    heat: Mapping[str, Any],
    flow: Mapping[str, Any],
    position: Mapping[str, Any],
    percentiles: Mapping[str, Mapping[str, float]],
    session_kind: str,
    trade_date: str | None,
) -> dict[str, Any]:
    return_5d = _number(position.get("return_5d_percent"))
    return_10d = _number(position.get("return_10d_percent"))
    return_20d = _number(position.get("return_20d_percent"))
    return_60d = _number(position.get("return_60d_percent"))
    relative_10d = _number(position.get("relative_return_10d_percent"))
    relative_20d = _number(position.get("relative_return_20d_percent"))
    relative_60d = _number(position.get("relative_return_60d_percent"))
    change_1d = _number(heat.get("change_1d_percent"))
    flow_today = _usable_flow(flow, "today_main_force_net_yi", "today_available")
    flow_5d = _usable_flow(flow, "cumulative_5d_net_yi", "five_day_available")
    flow_20d = _number(flow.get("cumulative_20d_net_yi")) if flow.get("date_aligned") is not False else None
    breadth = _number(heat.get("advancing_ratio_percent"))
    pattern = str(flow.get("pattern_label") or "").strip()

    relative_values: list[tuple[float, float]] = []
    for horizon, weight in (("10d", 0.20), ("20d", 0.45), ("60d", 0.35)):
        value = (percentiles.get(f"relative_{horizon}") or {}).get(label)
        if value is None:
            value = (percentiles.get(f"absolute_{horizon}") or {}).get(label)
        if value is not None:
            relative_values.append((value, weight))
    relative_score = _weighted_available(relative_values)

    trend_values: list[tuple[float, float]] = []
    if return_20d is not None:
        trend_values.append((_signed_score(return_20d, negative=-10.0, positive=12.0), 0.30))
    distance_ma20 = _number(position.get("distance_from_ma20_percent"))
    if distance_ma20 is not None:
        trend_values.append((_signed_score(distance_ma20, negative=-8.0, positive=8.0), 0.25))
    distance_ma60 = _number(position.get("distance_from_ma60_percent"))
    if distance_ma60 is not None:
        trend_values.append((_signed_score(distance_ma60, negative=-12.0, positive=12.0), 0.20))
    positive_days = _number(position.get("positive_day_ratio_20d_percent"))
    if positive_days is not None:
        trend_values.append((_clamp(positive_days, 0.0, 100.0), 0.25))
    trend_score = _weighted_available(trend_values)

    flow_values: list[tuple[float, float]] = []
    if flow_today is not None:
        flow_values.append((_signed_score(flow_today, negative=-3.0, positive=3.0), 0.20))
    for key, value, weight in (
        ("flow_5d", flow_5d, 0.35),
        ("flow_20d", flow_20d, 0.45),
    ):
        percentile = (percentiles.get(key) or {}).get(label)
        if percentile is not None and value is not None:
            directional = 100.0 if value > 0 else 0.0 if value < 0 else 50.0
            flow_values.append((percentile * 0.6 + directional * 0.4, weight))
    flow_score = _weighted_available(flow_values)

    breadth_score = _clamp(breadth, 0.0, 100.0) if breadth is not None else None
    structure_values: list[tuple[float, float]] = []
    drawdown_20d = _number(position.get("max_drawdown_20d_percent"))
    if drawdown_20d is not None:
        structure_values.append((_clamp(100.0 - drawdown_20d * 5.0, 0.0, 100.0), 0.35))
    distance_high = _number(position.get("distance_from_20d_high_percent"))
    if distance_high is not None:
        structure_values.append((_clamp(100.0 + distance_high * 5.0, 0.0, 100.0), 0.30))
    volume_ratio = _number(position.get("volume_ratio_5d_vs_20d"))
    if volume_ratio is not None:
        structure_values.append((_clamp(50.0 + (volume_ratio - 1.0) * 50.0, 0.0, 100.0), 0.20))
    volatility_20d = _number(position.get("annualized_volatility_20d_percent"))
    if volatility_20d is not None:
        structure_values.append((_clamp(100.0 - max(0.0, volatility_20d - 20.0) * 1.6, 0.0, 100.0), 0.15))
    structure_score = _weighted_available(structure_values)

    component_scores = {
        "relative_strength": relative_score,
        "trend_persistence": trend_score,
        "fund_flow": flow_score,
        "breadth": breadth_score,
        "market_structure": structure_score,
    }
    available_weight = sum(
        _COMPONENT_WEIGHTS[key]
        for key, value in component_scores.items()
        if value is not None
    )
    raw_score = (
        sum(
            float(value) * _COMPONENT_WEIGHTS[key]
            for key, value in component_scores.items()
            if value is not None
        )
        / available_weight
        if available_weight > 0
        else None
    )
    penalty, risk_flags = _risk_penalty(
        change_1d=change_1d,
        return_5d=return_5d,
        distance_high=distance_high,
        volume_ratio=volume_ratio,
        pattern=pattern,
    )
    position_source = str(position.get("source") or "").strip()
    if "proxy" in position_source:
        risk_flags.append("价格强度采用当前大市值成分股代理，非官方板块指数")
    score = _clamp((raw_score or 0.0) - penalty, 0.0, 100.0) if raw_score is not None else None
    coverage = round(available_weight, 2)
    status = _classify_status(
        position_available=bool(position.get("available")),
        coverage=coverage,
        raw_score=raw_score,
        score=score,
        relative_score=relative_score,
        trend_score=trend_score,
        relative_10d=relative_10d,
        relative_20d=relative_20d,
        return_10d=return_10d,
        return_60d=return_60d,
        flow_5d=flow_5d,
        flow_20d=flow_20d,
        pattern=pattern,
        penalty=penalty,
    )
    confidence = _confidence(
        coverage=coverage,
        position=position,
        session_kind=session_kind,
        status=status,
    )
    evidence = _evidence_lines(
        relative_20d=relative_20d,
        return_20d=return_20d,
        relative_score=relative_score,
        flow_5d=flow_5d,
        flow_20d=flow_20d,
        breadth=breadth,
        distance_ma20=distance_ma20,
    )
    if status == "insufficient":
        risk_flags.insert(0, "20日价格结构或多维证据覆盖不足，仅保留研究观察")

    return {
        "schema_version": MAINLINE_REGIME_SCHEMA_VERSION,
        "policy_version": MAINLINE_POLICY_VERSION,
        "sector_label": label,
        "as_of_trade_date": trade_date,
        "status": status,
        "score": round(score, 2) if score is not None else None,
        "confidence": confidence,
        "feature_coverage": coverage,
        "research_ranking_only": True,
        "execution_eligible": False,
        "automatic_promotion_allowed": False,
        "component_scores": {
            key: round(value, 2) if value is not None else None
            for key, value in component_scores.items()
        },
        "risk_penalty": round(penalty, 2),
        "features": {
            "change_1d_percent": change_1d,
            "return_5d_percent": return_5d,
            "return_10d_percent": return_10d,
            "return_20d_percent": return_20d,
            "return_60d_percent": return_60d,
            "relative_return_10d_percent": relative_10d,
            "relative_return_20d_percent": relative_20d,
            "relative_return_60d_percent": relative_60d,
            "relative_strength_percentile": round(relative_score, 2) if relative_score is not None else None,
            "today_main_force_net_yi": flow_today,
            "cumulative_5d_net_yi": flow_5d,
            "cumulative_20d_net_yi": flow_20d,
            "advancing_ratio_percent": breadth,
            "distance_from_ma20_percent": distance_ma20,
            "distance_from_ma60_percent": distance_ma60,
            "distance_from_20d_high_percent": distance_high,
            "volume_ratio_5d_vs_20d": volume_ratio,
            "max_drawdown_20d_percent": drawdown_20d,
            "position_label": position.get("position_label"),
            "breakout_over_prior_20d_high_percent": _number(
                position.get("breakout_over_prior_20d_high_percent")
            ),
            "up_days_5d": position.get("up_days_5d"),
            "down_days_5d": position.get("down_days_5d"),
        },
        "benchmark": {
            "code": position.get("benchmark_code"),
            "name": position.get("benchmark_name"),
            "source": position.get("benchmark_source"),
            "data_end_date": position.get("benchmark_data_end_date"),
        },
        "source_dates": {
            "sector_kline_end_date": position.get("data_end_date"),
            "sector_price_source": position_source or None,
            "proxy_member_count": position.get("proxy_member_count"),
            "flow_date": flow.get("flow_date"),
        },
        "evidence": evidence[:6],
        "risks": _unique(risk_flags)[:6],
    }


def _build_percentile_inputs(
    labels: Sequence[str],
    positions: Mapping[str, Mapping[str, Any]],
    flows: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for horizon in ("10d", "20d", "60d"):
        relative = {
            label: value
            for label in labels
            if (value := _number((positions.get(label) or {}).get(f"relative_return_{horizon}_percent"))) is not None
        }
        absolute = {
            label: value
            for label in labels
            if (value := _number((positions.get(label) or {}).get(f"return_{horizon}_percent"))) is not None
        }
        result[f"relative_{horizon}"] = _percentiles(relative)
        result[f"absolute_{horizon}"] = _percentiles(absolute)
    for key, field in (("flow_5d", "cumulative_5d_net_yi"), ("flow_20d", "cumulative_20d_net_yi")):
        values = {
            label: value
            for label in labels
            if (flows.get(label) or {}).get("date_aligned") is not False
            and (value := _number((flows.get(label) or {}).get(field))) is not None
        }
        result[key] = _percentiles(values)
    return result


def _percentiles(values: Mapping[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = list(values.values())
    size = len(ordered)
    result: dict[str, float] = {}
    for label, value in values.items():
        less = sum(item < value for item in ordered)
        equal = sum(item == value for item in ordered)
        result[label] = round((less + equal * 0.5) / size * 100.0, 2)
    return result


def _classify_status(
    *,
    position_available: bool,
    coverage: float,
    raw_score: float | None,
    score: float | None,
    relative_score: float | None,
    trend_score: float | None,
    relative_10d: float | None,
    relative_20d: float | None,
    return_10d: float | None,
    return_60d: float | None,
    flow_5d: float | None,
    flow_20d: float | None,
    pattern: str,
    penalty: float,
) -> str:
    if not position_available or coverage < 0.55 or score is None:
        return "insufficient"
    distribution = pattern in {"distribution", "weak_outflow", "multi_day_inflow_then_outflow"}
    long_term_positive = (return_60d or 0.0) > 0 or (relative_20d or 0.0) > 0
    short_term_negative = (relative_10d if relative_10d is not None else return_10d or 0.0) < 0
    flow_weak = distribution or (flow_5d is not None and flow_5d < 0) or (flow_20d is not None and flow_20d < 0)
    if long_term_positive and short_term_negative and flow_weak:
        return "fading"
    if (raw_score or 0.0) >= 65.0 and penalty >= 15.0:
        return "crowded"
    if (
        score >= 65.0
        and (relative_score or 0.0) >= 60.0
        and (trend_score or 0.0) >= 55.0
        and relative_20d is not None
        and relative_20d > 0
        and not distribution
    ):
        return "confirmed"
    short_improving = (relative_10d or 0.0) > 0 or (return_10d or 0.0) > 0
    flow_improving = (flow_5d or 0.0) > 0 or pattern in {
        "accumulation",
        "multi_day_outflow_then_inflow",
        "flow_turning_positive",
    }
    if score >= 48.0 and (trend_score or 0.0) >= 42.0 and (short_improving or flow_improving):
        return "forming"
    return "neutral"


def _risk_penalty(
    *,
    change_1d: float | None,
    return_5d: float | None,
    distance_high: float | None,
    volume_ratio: float | None,
    pattern: str,
) -> tuple[float, list[str]]:
    penalty = 0.0
    risks: list[str] = []
    if change_1d is not None and change_1d >= 4.0:
        penalty += 8.0
        risks.append("单日涨幅过热，存在追高风险")
    if return_5d is not None and return_5d >= 12.0:
        penalty += 10.0
        risks.append("近5日上涨加速，主线可能进入拥挤阶段")
    if distance_high is not None and distance_high >= -1.5 and (return_5d or 0.0) >= 6.0:
        penalty += 7.0
        risks.append("接近20日高位且短期涨幅较大")
    if volume_ratio is not None and volume_ratio >= 1.8 and (return_5d or 0.0) >= 6.0:
        penalty += 5.0
        risks.append("量能显著放大，需防冲高回落")
    if pattern in {"distribution", "weak_outflow", "multi_day_inflow_then_outflow"}:
        penalty += 18.0
        risks.append("价格与主力资金出现派发或转弱信号")
    return min(penalty, 40.0), risks


def _confidence(
    *,
    coverage: float,
    position: Mapping[str, Any],
    session_kind: str,
    status: str,
) -> str:
    if status == "insufficient" or coverage < 0.65:
        return "低"
    final_session = session_kind not in {"trading_day_intraday", "trading_day_pre_close"}
    benchmark_ready = _number(position.get("relative_return_20d_percent")) is not None
    sample_days = int(_number(position.get("sample_days")) or 0)
    if "proxy" in str(position.get("source") or ""):
        return "中"
    if coverage >= 0.85 and final_session and benchmark_ready and sample_days >= 61:
        return "高"
    return "中"


def _evidence_lines(
    *,
    relative_20d: float | None,
    return_20d: float | None,
    relative_score: float | None,
    flow_5d: float | None,
    flow_20d: float | None,
    breadth: float | None,
    distance_ma20: float | None,
) -> list[str]:
    lines: list[str] = []
    if relative_20d is not None:
        lines.append(f"近20日相对沪深300超额 {relative_20d:+.2f}%")
    elif return_20d is not None:
        lines.append(f"近20日板块收益 {return_20d:+.2f}%（宽基对齐暂缺）")
    if relative_score is not None:
        lines.append(f"多周期相对强度处于板块横截面 {relative_score:.1f} 分位")
    if flow_5d is not None:
        lines.append(f"近5日主力净流入 {flow_5d:+.2f} 亿元")
    if flow_20d is not None:
        lines.append(f"近20日主力净流入 {flow_20d:+.2f} 亿元")
    if breadth is not None:
        lines.append(f"当日上涨家数占比 {breadth:.1f}%")
    if distance_ma20 is not None:
        lines.append(f"板块收盘较20日均线 {distance_ma20:+.2f}%")
    return lines


def _benchmark_summary(regimes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in regimes:
        benchmark = row.get("benchmark")
        if isinstance(benchmark, Mapping) and benchmark.get("code"):
            return dict(benchmark)
    return {
        "code": "000300",
        "name": "沪深300",
        "source": None,
        "data_end_date": None,
    }


def _rows_by_label(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        label = str(row.get("sector_label") or "").strip()
        if label:
            result[label] = dict(row)
    return result


def _ordered_labels(
    labels: Sequence[str] | None,
    *maps: Mapping[str, Any],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in labels or []:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    for mapping in maps:
        for raw in mapping:
            label = str(raw or "").strip()
            if label and label not in seen:
                seen.add(label)
                result.append(label)
    return result


def _usable_flow(flow: Mapping[str, Any], value_key: str, available_key: str) -> float | None:
    if flow.get("available") is False or flow.get("date_aligned") is False:
        return None
    if available_key in flow and flow.get(available_key) is not True:
        return None
    return _number(flow.get(value_key))


def _weighted_available(values: Sequence[tuple[float, float]]) -> float | None:
    if not values:
        return None
    total_weight = sum(weight for _value, weight in values)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in values) / total_weight


def _signed_score(value: float, *, negative: float, positive: float) -> float:
    if positive <= negative:
        return 50.0
    return _clamp((value - negative) / (positive - negative) * 100.0, 0.0, 100.0)


def _status_priority(status: str) -> int:
    return {
        "confirmed": 6,
        "forming": 5,
        "crowded": 4,
        "neutral": 3,
        "fading": 2,
        "insufficient": 1,
    }.get(status, 0)


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "MAINLINE_POLICY_VERSION",
    "MAINLINE_REGIME_SCHEMA_VERSION",
    "MAINLINE_SNAPSHOT_SCHEMA_VERSION",
    "align_sector_opportunities_with_mainline_snapshot",
    "build_mainline_regime_snapshot",
    "mainline_regime_by_label",
]
