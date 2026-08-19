from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from math import sqrt
from typing import Literal

SelectionStrategy = Literal["balanced", "with_new_issue"]

_NEW_ISSUE_MAX_AGE_DAYS = 180
_NEW_ISSUE_SLOTS = 2
_PER_SECTOR = 5
OPPORTUNITY_SCORE_VERSION = "opportunity_20_60d.v2"
FUND_ENTRY_POLICY_VERSION = "fund_entry_position.2026-08.v2"


def balanced_score(row: dict) -> float:
    """Score higher for recent 3/6-month strength; one-year returns are ignored."""
    r6m = _num(row.get("return_6m_percent")) or 0.0
    r3m = _num(row.get("return_3m_percent"))
    if r3m is None:
        r3m = r6m

    recent_strength = r3m * 0.45 + r6m * 0.35

    nav_trend = row.get("nav_trend") or {}
    dist_high = _num(nav_trend.get("distance_from_high_percent"))
    room_bonus = 0.0
    if dist_high is not None and dist_high < 0:
        room_bonus = min(12.0, abs(dist_high) * 0.25)

    return recent_strength + room_bonus


def rank_candidates_balanced(candidates: list[dict]) -> list[dict]:
    scored = [(balanced_score(item), item) for item in candidates]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


def current_opportunity_score(row: dict) -> float | None:
    """Uncapped upside/elasticity ranking score for the 20--60 day horizon.

    The previous policy capped positive 20/60-day returns, rewarded shallow
    drawdowns and penalized funds near their highs.  That collapsed genuinely
    high-elasticity funds into the same score band as stable funds.  V2 keeps
    raw positive momentum, explicitly rewards realised volatility and a
    repaired pullback, and leaves drawdown to disclosure/allocation instead of
    subtracting it from the opportunity rank.

    The score is intentionally *not* capped at 100: it is a cross-candidate
    ordering value, not a probability or a promised return.
    """

    nav_trend = row.get("nav_trend")
    if not isinstance(nav_trend, dict):
        return None
    r5 = _num(nav_trend.get("recent_5d_change_percent"))
    r20 = _num(nav_trend.get("return_20d_percent"))
    r60 = _num(nav_trend.get("return_60d_percent"))
    volatility_20d = _num(nav_trend.get("annualized_volatility_20d_percent"))
    volatility_60d = _num(nav_trend.get("annualized_volatility_60d_percent"))
    recovery_20d = _num(nav_trend.get("drawdown_recovery_20d_percent"))
    rebound_20d = _num(nav_trend.get("rebound_from_20d_low_percent"))
    if all(
        value is None
        for value in (
            r5,
            r20,
            r60,
            volatility_20d,
            volatility_60d,
            recovery_20d,
            rebound_20d,
        )
    ):
        return None

    score = 20.0
    if r20 is not None:
        score += r20 * 1.4
    if r60 is not None:
        score += r60 * 0.65
    if r5 is not None:
        score += r5 * 2.2
    if volatility_20d is not None:
        score += max(0.0, volatility_20d) * 0.45
    if volatility_60d is not None:
        score += max(0.0, volatility_60d) * 0.15
    if recovery_20d is not None:
        score += max(0.0, recovery_20d - 40.0) * 0.25
    if rebound_20d is not None:
        score += max(0.0, rebound_20d) * 0.8

    # A rebound is useful only after price has actually left the trough and
    # the latest week has turned upward.  Negative medium-window return is not
    # a veto: it is precisely where a repaired high-elasticity setup can start.
    if (
        r5 is not None
        and r5 > 0
        and recovery_20d is not None
        and recovery_20d >= 50.0
        and rebound_20d is not None
        and rebound_20d >= 3.0
    ):
        score += 12.0
        if r20 is not None and r20 < 0:
            score += 8.0
    if r20 is not None and r60 is not None and r20 > 0 and r60 > 0 and (r5 or 0) >= 0:
        score += 8.0
    return round(max(0.0, score), 2)


def recall_upside_score(row: Mapping[str, object]) -> float:
    """Pre-enrichment score that prevents stable funds monopolising recall.

    Full 20/60-day NAV volatility is not available yet at this stage.  Recent
    3/6-month momentum is the recall proxy. Hard maturity, scale and
    research-quality checks still run later; this score only decides which
    funds receive the more expensive NAV enrichment.
    """

    current = current_opportunity_score(dict(row))
    if current is not None:
        return current
    r3 = _num(row.get("return_3m_percent"))
    r6 = _num(row.get("return_6m_percent"))
    score = 0.0
    if r3 is not None:
        score += r3 * 1.2
    if r6 is not None:
        score += r6 * 0.55
    if r3 is not None and r3 > 0 and r6 is not None and r6 < 0:
        score += 10.0
    return round(score, 2)


def assess_fund_entry_position(row: Mapping[str, object]) -> dict[str, object]:
    """Classify whether a fund's own NAV has repaired enough for entry.

    This is deliberately separate from the sector position.  A sector can be
    below its entry line while a particular fund has already repaired more
    than half of its 20-day range with broad positive days.  That fund-level
    fact may override *only* the sector position block; it cannot override a
    weak trend, weak participation, bad data or transaction gates.
    """

    nav = row.get("nav_trend")
    if not isinstance(nav, Mapping):
        return {
            "policy_version": FUND_ENTRY_POLICY_VERSION,
            "status": "insufficient",
            "entry_ready": False,
            "reason": "缺少20日净值修复数据",
        }
    r5 = _num(nav.get("recent_5d_change_percent"))
    r20 = _num(nav.get("return_20d_percent"))
    r60 = _num(nav.get("return_60d_percent"))
    recovery = _num(nav.get("drawdown_recovery_20d_percent"))
    rebound = _num(nav.get("rebound_from_20d_low_percent"))
    volatility = _num(nav.get("annualized_volatility_20d_percent"))
    distance_high_20d = _num(nav.get("distance_from_20d_high_percent"))
    daily = nav.get("recent_5d_daily_change_percent")
    daily_values = [
        value
        for raw in daily
        if (value := _num(raw)) is not None
    ] if isinstance(daily, list) else []
    positive_days = sum(value > 0 for value in daily_values)
    breadth_confirmed = len(daily_values) < 3 or positive_days >= 3
    latest_daily = daily_values[-1] if daily_values else None
    daily_sigma = volatility / sqrt(252.0) if volatility is not None and volatility > 0 else None
    latest_daily_sigma = (
        latest_daily / daily_sigma
        if latest_daily is not None and daily_sigma is not None and daily_sigma > 0
        else None
    )

    overheat_flags: list[str] = []
    if latest_daily is not None and latest_daily >= 4.0:
        overheat_flags.append("单日净值涨幅超过4%，短期加速")
    if latest_daily_sigma is not None and latest_daily_sigma >= 2.0:
        overheat_flags.append("单日净值涨幅超过20日波动的2个标准差")
    if r5 is not None and r5 >= 12.0:
        overheat_flags.append("近5日净值涨幅超过12%，短期加速")
    if (
        distance_high_20d is not None
        and distance_high_20d >= -1.5
        and r5 is not None
        and r5 >= 6.0
    ):
        overheat_flags.append("贴近20日高点且近5日涨幅达到6%")

    recovery_ready = bool(
        r5 is not None
        and r5 >= 1.0
        and recovery is not None
        and recovery >= 55.0
        and rebound is not None
        and rebound >= 3.0
        and (r20 is None or r20 >= -12.0)
        and breadth_confirmed
    )
    momentum_ready = bool(
        r5 is not None
        and r5 >= 0.5
        and r20 is not None
        and r20 > 0
        and r60 is not None
        and r60 > 0
        and recovery is not None
        and recovery >= 60.0
        and breadth_confirmed
    )
    pullback_magnitude_ok = bool(
        latest_daily is not None
        and latest_daily < 0
        and (
            (
                latest_daily_sigma is not None
                and latest_daily_sigma >= -1.5
            )
            or (
                latest_daily_sigma is None
                and latest_daily >= -2.5
            )
        )
    )
    benign_pullback_ready = bool(
        pullback_magnitude_ok
        and r5 is not None
        and r5 >= -3.0
        and r20 is not None
        and r20 > 0
        and r60 is not None
        and r60 > 0
        and recovery is not None
        and recovery >= 55.0
        and rebound is not None
        and rebound >= 3.0
        and (distance_high_20d is None or distance_high_20d >= -10.0)
        and (len(daily_values) < 3 or positive_days >= 2)
    )
    early_daily_ok = bool(
        latest_daily is None
        or (
            latest_daily_sigma is not None
            and latest_daily_sigma >= -1.2
        )
        or (
            latest_daily_sigma is None
            and latest_daily >= -2.0
        )
    )
    early_probe_ready = bool(
        r5 is not None
        and r5 >= 0.0
        and recovery is not None
        and recovery >= 45.0
        and rebound is not None
        and rebound >= 2.0
        and (r20 is None or r20 >= -10.0)
        and early_daily_ok
        and (len(daily_values) < 3 or positive_days >= 2)
    )
    entry_ready = recovery_ready or momentum_ready or benign_pullback_ready
    if benign_pullback_ready:
        status = "pullback_ready"
        entry_path = "benign_pullback"
        reason = "20/60日趋势未破，温和回调处于正常波动内且20日修复保持过半"
    elif recovery_ready and not momentum_ready:
        status = "recovery_ready"
        entry_path = "recovery_confirmation"
        reason = "20日回撤已修复过半，近5日转强且上涨日占优"
    elif momentum_ready:
        status = "momentum_ready"
        entry_path = "momentum_confirmation"
        reason = "5/20/60日趋势同向，20日价格修复已通过"
    elif r5 is not None and r5 <= 0 and (recovery or 0.0) < 45.0:
        status = "falling"
        entry_path = "forming"
        reason = "仍靠近20日低位且近5日尚未转强"
    else:
        status = "forming"
        entry_path = "forming"
        reason = "价格正在修复，但尚未同时通过修复幅度和近5日确认"

    overheat_scale = (
        0.4 if len(overheat_flags) >= 2 else 0.6 if overheat_flags else 1.0
    )
    first_tranche_scale = min(
        overheat_scale,
        0.5 if benign_pullback_ready else 1.0,
        0.4 if early_probe_ready and not entry_ready else 1.0,
    )

    return {
        "policy_version": FUND_ENTRY_POLICY_VERSION,
        "status": status,
        "entry_path": entry_path,
        "entry_ready": entry_ready,
        "early_probe_ready": early_probe_ready,
        "early_probe_reason": (
            "20日修复已达到45%，近5日未转弱且最近单日下跌仍在正常承接范围"
            if early_probe_ready
            else "基金自身尚未达到提前试仓所需的早期修复条件"
        ),
        "first_tranche_scale": first_tranche_scale,
        "high_elasticity": volatility is not None and volatility >= 24.0,
        "overheat_flags": overheat_flags,
        "reason": reason,
        "components": {
            "recent_5d_change_percent": r5,
            "return_20d_percent": r20,
            "return_60d_percent": r60,
            "drawdown_recovery_20d_percent": recovery,
            "rebound_from_20d_low_percent": rebound,
            "annualized_volatility_20d_percent": volatility,
            "distance_from_20d_high_percent": distance_high_20d,
            "positive_days_5d": positive_days if daily_values else None,
            "latest_daily_change_percent": latest_daily,
            "daily_sigma_percent": round(daily_sigma, 4) if daily_sigma is not None else None,
            "latest_daily_move_sigma": (
                round(latest_daily_sigma, 4) if latest_daily_sigma is not None else None
            ),
        },
        "thresholds": {
            "minimum_recovery_percent": 55.0,
            "minimum_rebound_from_low_percent": 3.0,
            "minimum_recent_5d_change_percent": 1.0,
            "minimum_positive_days_5d": 3,
            "pullback_minimum_recent_5d_change_percent": -3.0,
            "pullback_maximum_daily_sigma": 1.5,
            "pullback_minimum_recovery_percent": 55.0,
            "early_probe_minimum_recovery_percent": 45.0,
            "early_probe_minimum_rebound_percent": 2.0,
            "early_probe_minimum_recent_5d_change_percent": 0.0,
        },
        "invalidation_signals": [
            "近5日收益重新转负且20日修复率跌回40%以下",
            "净值重新跌回本轮20日低位区域",
        ],
    }


def fund_entry_opens_v3_improving_flow_probe(
    candidate: Mapping[str, object],
    opportunity: Mapping[str, object] | None,
) -> bool:
    """Allow a reduced first tranche when current flow turns before history catches up."""

    if not isinstance(opportunity, Mapping):
        return False
    if str(opportunity.get("score_policy_version") or "") != "sector_entry_maturity.2026-08.v3":
        return False
    if opportunity.get("flow_improving_probe_eligible") is not True:
        return False
    signal = candidate.get("fund_entry_signal")
    return bool(isinstance(signal, Mapping) and signal.get("entry_ready") is True)


def fund_entry_opens_v3_probability_probe(
    candidate: Mapping[str, object],
    opportunity: Mapping[str, object] | None,
) -> bool:
    """Open a probability-sized probe only after the fund confirms early repair."""

    if not isinstance(opportunity, Mapping):
        return False
    if str(opportunity.get("score_policy_version") or "") != "sector_entry_maturity.2026-08.v3":
        return False
    if opportunity.get("probability_early_probe_eligible") is not True:
        return False
    signal = candidate.get("fund_entry_signal")
    return bool(
        isinstance(signal, Mapping)
        and (
            signal.get("entry_ready") is True
            or signal.get("early_probe_ready") is True
        )
    )


def fund_recovery_overrides_sector_position(
    candidate: Mapping[str, object],
    opportunity: Mapping[str, object] | None,
) -> bool:
    """Whether fund repair may replace only a failed V3 sector-position gate."""

    if not isinstance(opportunity, Mapping):
        return False
    if str(opportunity.get("score_policy_version") or "") != "sector_entry_maturity.2026-08.v3":
        return False
    if str(opportunity.get("entry_state") or "") != "ready_on_pullback":
        return False
    signal = candidate.get("fund_entry_signal")
    if not isinstance(signal, Mapping) or signal.get("entry_ready") is not True:
        return False
    trend = _num(opportunity.get("trend_strength_score"))
    participation = _num(opportunity.get("participation_score"))
    position = _num(opportunity.get("position_risk_score"))
    gate_inputs = opportunity.get("entry_gate_inputs")
    mainline_status = (
        str(gate_inputs.get("mainline_status") or "")
        if isinstance(gate_inputs, Mapping)
        else ""
    )
    return bool(
        trend is not None
        and trend >= 60.0
        and participation is not None
        and participation >= 35.0
        and position is not None
        and position < 25.0
        and mainline_status in {"forming", "confirmed", "crowded"}
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def pick_sector_candidates(
    *,
    sector_label: str,
    fixed_entries: list[dict],
    ranked_entries: list[dict],
    new_issue_rows: list[dict],
    keywords: tuple[str, ...],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    selection_strategy: SelectionStrategy,
    name_matches_sector,
    matches_fund_type,
    as_of_date: date | None = None,
) -> list[dict]:
    """Pick up to _PER_SECTOR entries for one sector."""
    results = list(fixed_entries)
    remaining = max(_PER_SECTOR - len(results), 0)
    if remaining <= 0:
        return results[:_PER_SECTOR]

    if selection_strategy == "with_new_issue":
        new_picks = _pick_new_issue_for_sector(
            new_issue_rows,
            sector_label=sector_label,
            keywords=keywords,
            excluded=excluded,
            seen_codes=seen_codes,
            fund_type_preference=fund_type_preference,
            limit=min(_NEW_ISSUE_SLOTS, remaining),
            name_matches_sector=name_matches_sector,
            matches_fund_type=matches_fund_type,
            as_of_date=as_of_date,
        )
        results.extend(new_picks)
        remaining = max(_PER_SECTOR - len(results), 0)

    if remaining <= 0:
        return results[:_PER_SECTOR]

    ranked = rank_candidates_balanced(ranked_entries)
    for entry in ranked:
        code = str(entry.get("fund_code", "")).zfill(6)
        if code in seen_codes:
            continue
        results.append(entry)
        seen_codes.add(code)
        remaining -= 1
        if remaining <= 0:
            break

    return results[:_PER_SECTOR]


def _pick_new_issue_for_sector(
    rows: list[dict],
    *,
    sector_label: str,
    keywords: tuple[str, ...],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    limit: int,
    name_matches_sector,
    matches_fund_type,
    as_of_date: date | None = None,
) -> list[dict]:
    if limit <= 0:
        return []

    cutoff = (as_of_date or date.today()) - timedelta(days=_NEW_ISSUE_MAX_AGE_DAYS)
    picks: list[dict] = []
    for row in rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if not code.isdigit() or len(code) != 6:
            continue
        if code in excluded or code in seen_codes:
            continue
        name = str(row.get("fund_name", ""))
        if not name_matches_sector(name, keywords):
            continue
        if not matches_fund_type(name, fund_type_preference):
            continue
        established = _parse_date(row.get("established_date"))
        if established is not None and established < cutoff:
            continue
        entry = {
            "fund_code": code,
            "fund_name": name,
            "sector_label": sector_label,
            "selection_reason": "新发观察",
            "is_new_issue": True,
            "established_date": established.isoformat() if established else row.get("established_date"),
            "return_since_issue_percent": row.get("return_since_issue_percent"),
        }
        picks.append(entry)
        seen_codes.add(code)
        if len(picks) >= limit:
            break
    return picks


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    if not text:
        return None
    normalized = text.replace("/", "-")
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None
