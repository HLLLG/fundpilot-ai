from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.services.fund_nav_service import get_cached_official_nav_return
from app.services.fund_peer_ranking import compact_peer_research_for_llm
from app.services.sector_labels import normalize_sector_label
from app.services.trading_session import CN_TZ

_NAV_TREND_LLM_KEYS = (
    "trend_label",
    "recent_5d_change_percent",
    "recent_5d_daily_change_percent",
    "return_20d_percent",
    "max_drawdown_20d_percent",
    "annualized_volatility_20d_percent",
    "distance_from_20d_high_percent",
    "rebound_from_20d_low_percent",
    "drawdown_recovery_20d_percent",
    "return_60d_percent",
    "max_drawdown_60d_percent",
    "annualized_volatility_60d_percent",
    "drawdown_recovery_60d_percent",
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
    result = {}
    for key in _QUALITY_GATE_SCALAR_LLM_KEYS:
        if key not in value:
            continue
        scalar = _scalar(value.get(key))
        if scalar is not None:
            result[key] = scalar
    for key in ("reasons", "missing_fields", "profile_sources", "profile_stale_fields"):
        if key in value:
            result[key] = _text_list(value.get(key))
    return result


def slim_nav_trend_for_llm(nav_trend: dict | None) -> dict | None:
    if not isinstance(nav_trend, dict):
        return None
    slim = {key: nav_trend[key] for key in _NAV_TREND_LLM_KEYS if nav_trend.get(key) is not None}
    return slim or None


def format_change_as_of_time(value: object) -> str | None:
    """板块涨跌的截止时刻 → 北京时间 ``HH:MM``。

    盘中扫描给出的"今日涨跌估算"本质是某一时刻的板块快照，不是收盘值。报告里必须带上
    这个时刻，用户才能拿它去和基金详情页的板块分时对照，判断是不是已经过时。
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(CN_TZ).strftime("%H:%M")


def build_sector_change_as_of_index(sector_heat: list[dict]) -> dict[str, str]:
    """与 ``build_sector_change_index`` 同一套 label/规范化双键，值换成截止时刻。"""
    index: dict[str, str] = {}
    for row in sector_heat:
        label = str(row.get("sector_label") or "").strip()
        as_of = format_change_as_of_time(row.get("change_as_of"))
        if not label or not as_of:
            continue
        index[label] = as_of
        normalized = normalize_sector_label(label)
        if normalized and normalized not in index:
            index[normalized] = as_of
    return index


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
    sector_change_as_of_index: dict[str, str] | None = None,
) -> tuple[float | None, str | None, str | None]:
    """返回 ``(涨跌, 口径, 截止时刻)``；截止时刻只对板块估算口径有意义。

    官方净值是当日收盘后一次性公布的，没有"截至几点"可言，所以只有 ``sector_estimate``
    会带上时刻。
    """
    code = str(fund_code or "").strip().zfill(6)
    if trade_date and code and code != "000000":
        cached = get_cached_official_nav_return(code, trade_date)
        if cached is not None:
            return round(float(cached), 4), "official_nav", None

    label = str(sector_label or "").strip()
    as_of_index = sector_change_as_of_index or {}
    for key in (label, normalize_sector_label(label) if label else ""):
        if key and key in sector_change_index:
            return round(sector_change_index[key], 4), "sector_estimate", as_of_index.get(key)
    return None, None, None


def slim_candidate_for_llm(
    item: dict,
    *,
    sector_change_index: dict[str, float],
    trade_date: str | None,
    sector_change_as_of_index: dict[str, str] | None = None,
) -> dict:
    code = item.get("fund_code")
    sector = item.get("sector_label")
    daily, source, daily_as_of = resolve_candidate_daily_estimate(
        fund_code=str(code or ""),
        sector_label=str(sector or ""),
        sector_change_index=sector_change_index,
        trade_date=trade_date,
        sector_change_as_of_index=sector_change_as_of_index,
    )
    scalar_fields = (
        "fund_code",
        "fund_name",
        "sector_label",
        "sector_match_kind",
        "sector_identity_status",
        "sector_identity_eligible",
        "sector_mapping_verified",
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
        "fund_quality_score",
        "recall_upside_score",
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
    row: dict = {}
    for key in scalar_fields:
        scalar = _scalar(item.get(key))
        if scalar is not None:
            row[key] = scalar
    quality_components: dict[str, object] = {}
    raw_quality_components = item.get("quality_score_components")
    if isinstance(raw_quality_components, dict):
        for key in _QUALITY_SCORE_COMPONENT_LLM_KEYS:
            scalar = _scalar(raw_quality_components.get(key))
            if scalar is not None:
                quality_components[key] = scalar
    row.update(
        {
            "profile_sources": _text_list(item.get("profile_sources")),
            "quality_score_components": quality_components,
            "quality_gate": _compact_quality_gate(item.get("quality_gate")),
            "quality_reasons": _text_list(item.get("quality_reasons")),
            "quality_penalties": _text_list(item.get("quality_penalties")),
            "vehicle_quality_assessment": _compact_vehicle_quality_assessment(
                item.get("vehicle_quality_assessment")
            ),
            "peer_research": _compact_peer_research(item),
            "benchmark_research": _compact_benchmark_research(item),
            "benchmark_metrics": _compact_benchmark_metrics(item),
            "fund_entry_signal": _compact_fund_entry_signal(
                item.get("fund_entry_signal")
            ),
        }
    )
    identity_mismatch = _compact_sector_identity_mismatch(
        item.get("sector_identity_mismatch")
    )
    if identity_mismatch:
        row["sector_identity_mismatch"] = identity_mismatch
    nav = slim_nav_trend_for_llm(item.get("nav_trend"))
    if nav:
        row["nav_trend"] = nav
    if daily is not None:
        row["estimated_daily_return_percent"] = daily
        row["daily_return_source"] = source
        if daily_as_of:
            row["estimated_daily_return_as_of"] = daily_as_of
    return row


def _compact_sector_identity_mismatch(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: scalar
        for key in (
            "relation_kind",
            "target_sector_label",
            "verified_sector_label",
            "index_code",
            "index_name",
            "benchmark_text_source_kind",
            "exact",
        )
        if (scalar := _scalar(value.get(key))) is not None
    }


def _compact_fund_entry_signal(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    components = value.get("components") if isinstance(value.get("components"), Mapping) else {}
    thresholds = value.get("thresholds") if isinstance(value.get("thresholds"), Mapping) else {}
    return {
        "policy_version": _scalar(value.get("policy_version")),
        "status": _scalar(value.get("status")),
        "entry_path": _scalar(value.get("entry_path")),
        "entry_ready": value.get("entry_ready") is True,
        "early_probe_ready": value.get("early_probe_ready") is True,
        "early_probe_reason": _scalar(value.get("early_probe_reason")),
        "first_tranche_scale": _scalar(value.get("first_tranche_scale")),
        "high_elasticity": value.get("high_elasticity") is True,
        "overheat_flags": _text_list(value.get("overheat_flags")),
        "reason": _scalar(value.get("reason")),
        "components": {
            key: scalar
            for key, raw in components.items()
            if (scalar := _scalar(raw)) is not None
        },
        "thresholds": {
            key: scalar
            for key, raw in thresholds.items()
            if (scalar := _scalar(raw)) is not None
        },
        "invalidation_signals": _text_list(value.get("invalidation_signals")),
    }


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
    sector_change_as_of_index = build_sector_change_as_of_index(sector_heat)
    return [
        slim_candidate_for_llm(
            item,
            sector_change_index=sector_change_index,
            trade_date=trade_date,
            sector_change_as_of_index=sector_change_as_of_index,
        )
        for item in items
        if isinstance(item, dict)
    ]


def _compact_peer_research(item: dict) -> dict:
    # 投影本体已抽到 `fund_peer_ranking.compact_peer_research_for_llm`，日报给持仓算
    # 同类分位时复用同一份，避免两处各自挑字段而漂移。
    return compact_peer_research_for_llm(item)


def _compact_benchmark_research(item: dict) -> dict:
    comparison = (
        item.get("benchmark_comparison")
        if isinstance(item.get("benchmark_comparison"), dict)
        else {}
    )
    spec = item.get("benchmark_spec") if isinstance(item.get("benchmark_spec"), dict) else {}
    result = {
        "schema_version": comparison.get("schema_version"),
        "comparison_role": comparison.get("comparison_role"),
        "formal_excess_eligible": comparison.get("formal_excess_eligible") is True,
        "benchmark_code": comparison.get("benchmark_code") or spec.get("benchmark_code"),
        "benchmark_name": comparison.get("benchmark_name") or spec.get("benchmark_name"),
        "mapping_id": comparison.get("mapping_id"),
        "reason": comparison.get("reason") or spec.get("reason"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _compact_benchmark_metrics(item: dict) -> dict:
    # 投影本身随基准 schema 演进，实现放在契约所属模块，日报持仓行与荐基候选行共用。
    from app.services.fund_benchmark_research import (
        compact_fund_benchmark_metrics_for_llm,
    )

    return compact_fund_benchmark_metrics_for_llm(item.get("benchmark_metrics"))


def _present_scalars(
    value: Mapping[str, Any],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        scalar = _scalar(value.get(key))
        if scalar is not None:
            result[key] = scalar
    return result


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
    return [_project_sector_heat_as_of(row) for row in selected]


def _project_sector_heat_as_of(row: dict) -> dict:
    """把内部的 UTC ISO 截止时刻换成北京时间 ``HH:MM``，别让 LLM 自己做时区换算。"""
    if "change_as_of" not in row:
        return row
    projected = {key: value for key, value in row.items() if key != "change_as_of"}
    as_of = format_change_as_of_time(row.get("change_as_of"))
    if as_of:
        projected["change_as_of_time"] = as_of
    return projected
