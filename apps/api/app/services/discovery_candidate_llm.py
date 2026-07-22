from __future__ import annotations

from app.services.fund_nav_service import get_cached_official_nav_return
from app.services.fund_tradeability import compact_tradeability_for_llm
from app.services.sector_labels import normalize_sector_label

_NAV_TREND_LLM_KEYS = (
    "trend_label",
    "recent_5d_change_percent",
    "recent_5d_daily_change_percent",
    "return_20d_percent",
    "max_drawdown_20d_percent",
    "return_60d_percent",
    "max_drawdown_60d_percent",
    "distance_from_high_percent",
    "period_change_percent",
)

_QUALITY_SCORE_COMPONENT_LLM_KEYS = (
    "sector_fit",
    "performance",
    "drawdown_control",
    "scale",
    "data_completeness",
    "legacy_type_preference",
)
_QUALITY_GATE_SCALAR_LLM_KEYS = (
    "eligible",
    "status",
    "coverage_percent",
    "data_as_of",
    "profile_status",
    "profile_checked_at",
)


def _scalar(value: object) -> object | None:
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _text_list(value: object) -> list[str]:
    return [item for item in value or [] if isinstance(item, str)] if isinstance(value, list) else []


def _compact_quality_gate(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {
        key: _scalar(value.get(key))
        for key in _QUALITY_GATE_SCALAR_LLM_KEYS
        if key in value
    }
    for key in ("reasons", "missing_fields", "profile_sources", "profile_stale_fields"):
        if key in value:
            result[key] = _text_list(value.get(key))
    return result


def slim_nav_trend_for_llm(nav_trend: dict | None) -> dict | None:
    if not isinstance(nav_trend, dict):
        return None
    slim = {key: nav_trend[key] for key in _NAV_TREND_LLM_KEYS if nav_trend.get(key) is not None}
    return slim or None


def build_sector_change_index(sector_heat: list[dict]) -> dict[str, float]:
    index: dict[str, float] = {}
    for row in sector_heat:
        label = str(row.get("sector_label") or "").strip()
        change = row.get("change_1d_percent")
        if not label or change is None:
            continue
        try:
            value = float(change)
        except (TypeError, ValueError):
            continue
        index[label] = value
        normalized = normalize_sector_label(label)
        if normalized and normalized not in index:
            index[normalized] = value
    return index


def resolve_candidate_daily_estimate(
    *,
    fund_code: str,
    sector_label: str,
    sector_change_index: dict[str, float],
    trade_date: str | None,
) -> tuple[float | None, str | None]:
    code = str(fund_code or "").strip().zfill(6)
    if trade_date and code and code != "000000":
        cached = get_cached_official_nav_return(code, trade_date)
        if cached is not None:
            return round(float(cached), 4), "official_nav"

    label = str(sector_label or "").strip()
    for key in (label, normalize_sector_label(label) if label else ""):
        if key and key in sector_change_index:
            return round(sector_change_index[key], 4), "sector_estimate"
    return None, None


def slim_candidate_for_llm(
    item: dict,
    *,
    sector_change_index: dict[str, float],
    trade_date: str | None,
) -> dict:
    code = item.get("fund_code")
    sector = item.get("sector_label")
    daily, source = resolve_candidate_daily_estimate(
        fund_code=str(code or ""),
        sector_label=str(sector or ""),
        sector_change_index=sector_change_index,
        trade_date=trade_date,
    )
    scalar_fields = (
        "fund_code",
        "fund_name",
        "sector_label",
        "sector_match_kind",
        "return_1y_percent",
        "return_3m_percent",
        "return_6m_percent",
        "max_drawdown_1y_percent",
        "fund_scale_yi",
        "fund_scale_basis",
        "management_fee",
        "fund_type",
        "fund_manager",
        "established_date",
        "profile_updated_at",
        "profile_status",
        "share_class",
        "share_class_fee_status",
        "fund_quality_score",
        "vehicle_quality_score",
        "vehicle_quality_status",
        "vehicle_quality_threshold",
        "vehicle_quality_method",
        "vehicle_quality_version",
        "opportunity_score_20_60d",
        "opportunity_score_version",
        "sector_fit_score",
        "quality_score_version",
        "selection_reason",
        "candidate_universe_mode",
        "candidate_universe_size",
    )
    row: dict = {
        key: _scalar(item.get(key))
        for key in scalar_fields
    }
    row.update({
        "profile_sources": _text_list(item.get("profile_sources")),
        "tradeability": compact_tradeability_for_llm(item.get("tradeability")),
        "quality_score_components": {
            key: _scalar((item.get("quality_score_components") or {}).get(key))
            for key in _QUALITY_SCORE_COMPONENT_LLM_KEYS
            if isinstance(item.get("quality_score_components"), dict)
            and key in item["quality_score_components"]
        },
        "quality_gate": _compact_quality_gate(item.get("quality_gate")),
        "quality_reasons": _text_list(item.get("quality_reasons")),
        "quality_penalties": _text_list(item.get("quality_penalties")),
        "vehicle_quality_assessment": _compact_vehicle_quality_assessment(
            item.get("vehicle_quality_assessment")
        ),
        "peer_research": _compact_peer_research(item),
        "benchmark_research": _compact_benchmark_research(item),
        "benchmark_metrics": _compact_benchmark_metrics(item),
    })
    nav = slim_nav_trend_for_llm(item.get("nav_trend"))
    if nav:
        row["nav_trend"] = nav
    if daily is not None:
        row["estimated_daily_return_percent"] = daily
        row["daily_return_source"] = source
    return row


def _compact_vehicle_quality_assessment(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    components = value.get("components") if isinstance(value.get("components"), dict) else {}
    return {
        "schema_version": _scalar(value.get("schema_version")),
        "method": _scalar(value.get("method")),
        "status": _scalar(value.get("status")),
        "score": _scalar(value.get("score")),
        "threshold": _scalar(value.get("threshold")),
        "sector_fit_separate_gate": value.get("sector_fit_separate_gate") is True,
        "absolute_sector_return_excluded": value.get("absolute_sector_return_excluded") is True,
        "components": {key: _scalar(component) for key, component in components.items()},
        "reasons": _text_list(value.get("reasons")),
        "penalties": _text_list(value.get("penalties")),
    }


def slim_candidate_pool_for_llm(
    items: list[dict],
    *,
    sector_heat: list[dict],
    trade_date: str | None,
) -> list[dict]:
    """Use one explicit candidate projection for primary generation and judge."""

    sector_change_index = build_sector_change_index(sector_heat)
    return [
        slim_candidate_for_llm(
            item,
            sector_change_index=sector_change_index,
            trade_date=trade_date,
        )
        for item in items
        if isinstance(item, dict)
    ]


def _compact_peer_research(item: dict) -> dict:
    peer_rank = item.get("peer_rank") if isinstance(item.get("peer_rank"), dict) else {}
    peer_group = item.get("peer_group") if isinstance(item.get("peer_group"), dict) else {}
    metrics = peer_rank.get("metrics") if isinstance(peer_rank.get("metrics"), dict) else {}
    return {
        "schema_version": peer_rank.get("schema_version"),
        "status": peer_rank.get("status"),
        "execution_tilt_eligible": peer_rank.get("execution_tilt_eligible") is True,
        "group_key": peer_group.get("group_key"),
        "group_label": peer_group.get("group_label"),
        "classification_confidence": peer_group.get("classification_confidence"),
        "metric_registry_version": peer_rank.get("metric_registry_version"),
        "metric_profile": peer_rank.get("metric_profile"),
        "descriptive_performance_percentile": peer_rank.get(
            "descriptive_performance_percentile"
        ),
        "independent_peer_family_count": (
            peer_rank.get("universe") or {}
        ).get("independent_peer_family_count"),
        "metrics": {
            key: {
                "label": value.get("label"),
                "orientation": value.get("orientation"),
                "role": value.get("role"),
                "applicable": value.get("applicable") is True,
                "applicability": value.get("applicability"),
                "available": value.get("available") is True,
                "availability": value.get("availability"),
                "value": value.get("value"),
                "percentile": value.get("percentile"),
                "sample_count": value.get("sample_count"),
                "coverage_rate": value.get("coverage_rate"),
                "qualified": value.get("qualified") is True,
                "qualification_required": value.get("qualification_required") is True,
                "reason": value.get("reason"),
            }
            for key, value in metrics.items()
            if isinstance(value, dict)
        },
        "instruction": (
            "同类分位只用于同组研究解释；execution_tilt_eligible=false 时不得据此提高金额。"
        ),
    }


def _compact_benchmark_research(item: dict) -> dict:
    comparison = (
        item.get("benchmark_comparison")
        if isinstance(item.get("benchmark_comparison"), dict)
        else {}
    )
    spec = item.get("benchmark_spec") if isinstance(item.get("benchmark_spec"), dict) else {}
    return {
        "schema_version": comparison.get("schema_version"),
        "comparison_role": comparison.get("comparison_role"),
        "formal_excess_eligible": comparison.get("formal_excess_eligible") is True,
        "benchmark_code": comparison.get("benchmark_code") or spec.get("benchmark_code"),
        "benchmark_name": comparison.get("benchmark_name") or spec.get("benchmark_name"),
        "mapping_id": comparison.get("mapping_id"),
        "reason": comparison.get("reason") or spec.get("reason"),
        "instruction": (
            "仅 formal_excess_eligible=true 可称正式超额；tracking_reference 只能称跟踪参考。"
        ),
    }


def _compact_benchmark_metrics(item: dict) -> dict:
    metrics = (
        item.get("benchmark_metrics")
        if isinstance(item.get("benchmark_metrics"), dict)
        else {}
    )
    horizons = metrics.get("horizons") if isinstance(metrics.get("horizons"), dict) else {}
    rolling = (
        metrics.get("rolling_comparison")
        if isinstance(metrics.get("rolling_comparison"), dict)
        else {}
    )
    tracking = (
        metrics.get("tracking_metrics")
        if isinstance(metrics.get("tracking_metrics"), dict)
        else {}
    )
    alignment = metrics.get("alignment") if isinstance(metrics.get("alignment"), dict) else {}
    return {
        "schema_version": metrics.get("schema_version"),
        "status": metrics.get("status"),
        "qualified": metrics.get("qualified") is True,
        "descriptive_only": True,
        "execution_tilt_eligible": False,
        "comparison_role": metrics.get("comparison_role"),
        "formal_excess_eligible": metrics.get("formal_excess_eligible") is True,
        "benchmark_code": metrics.get("benchmark_code"),
        "benchmark_name": metrics.get("benchmark_name"),
        "effective_trade_date": metrics.get("effective_trade_date"),
        "reason_codes": list(metrics.get("reason_codes") or []),
        "alignment": {
            "common_return_sample_days": alignment.get("common_return_sample_days"),
            "first_common_date": alignment.get("first_common_date"),
            "last_common_date": alignment.get("last_common_date"),
        },
        "horizons": {
            key: {
                "status": value.get("status"),
                "start_date": value.get("start_date"),
                "end_date": value.get("end_date"),
                "fund_return_percent": value.get("fund_return_percent"),
                "benchmark_return_percent": value.get("benchmark_return_percent"),
                "formal_excess_return_percent": value.get(
                    "formal_excess_return_percent"
                ),
                "reference_difference_percent": value.get(
                    "reference_difference_percent"
                ),
                "fund_max_drawdown_percent": value.get(
                    "fund_max_drawdown_percent"
                ),
                "benchmark_max_drawdown_percent": value.get(
                    "benchmark_max_drawdown_percent"
                ),
                "drawdown_advantage_percent": value.get(
                    "drawdown_advantage_percent"
                ),
            }
            for key, value in horizons.items()
            if key in {"3m", "6m", "1y"} and isinstance(value, dict)
        },
        "rolling_comparison": {
            "window_days": rolling.get("window_days"),
            "window_count": rolling.get("window_count"),
            "formal_excess_win_rate_percent": rolling.get(
                "formal_excess_win_rate_percent"
            ),
            "reference_outperformance_rate_percent": rolling.get(
                "reference_outperformance_rate_percent"
            ),
            "difference_stability_percent": rolling.get(
                "difference_stability_percent"
            ),
        },
        "tracking_metrics": {
            "applicable": tracking.get("applicable") is True,
            "available": tracking.get("available") is True,
            "tracking_difference_percent": tracking.get(
                "tracking_difference_percent"
            ),
            "annualized_tracking_error_percent": tracking.get(
                "annualized_tracking_error_percent"
            ),
        },
        "instruction": (
            "只有 status=qualified 的数值可引用；formal_excess_eligible=true 才能称正式超额，"
            "tracking_reference 只能称跟踪参考；全部仅作描述，不得用于金额倾斜。"
        ),
    }


def trim_sector_heat_for_llm(
    sector_heat: list[dict],
    *,
    target_sectors: list[str],
    focus_sectors: list[str],
    top_n: int = 15,
) -> list[dict]:
    if not sector_heat:
        return []

    keep_labels = {
        str(label).strip()
        for label in (*target_sectors, *focus_sectors)
        if str(label).strip()
    }
    by_label = {
        str(row.get("sector_label") or "").strip(): dict(row)
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }

    selected: list[dict] = []
    seen: set[str] = set()
    for label in keep_labels:
        row = by_label.get(label)
        if row and label not in seen:
            selected.append(row)
            seen.add(label)

    ranked = sorted(
        sector_heat,
        key=lambda row: float(row.get("heat_score") or -999),
        reverse=True,
    )
    for row in ranked:
        if len(selected) >= top_n:
            break
        label = str(row.get("sector_label") or "").strip()
        if not label or label in seen:
            continue
        selected.append(dict(row))
        seen.add(label)
    return selected
