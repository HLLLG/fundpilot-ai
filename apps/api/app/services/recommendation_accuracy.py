from __future__ import annotations

from typing import Any, Iterable

from app.database import list_reports
from app.services.akshare_subprocess import fetch_fund_nav_history
from app.services.benchmark_fee_evaluation import (
    METRIC_CONTRACT_VERSION,
    METRIC_NAMES,
    BenchmarkFetcher,
    default_benchmark_fetcher,
)
from app.services.recommendation_forward_evaluation import (
    DEFAULT_HORIZONS,
    METRIC_STATUS,
    METRIC_VERSION,
    NavFetcher,
    deduplicate_reports_by_calendar_date,
    normalize_horizons,
    recommended_nav_pull_days,
)
from app.services.recommendation_outcomes import build_recommendation_outcomes
from app.services.trade_calendar_cache import get_trade_date_set


FORWARD_EVALUATION_WARNING = (
    "当前已改用基金估值日 T+N 成熟样本，并拆分方向、用户假设费后和正式基准超额；"
    "真实交易费率、正式基准覆盖及样本量达标前，仍只用于人工复盘，不进入自动调参。"
)


def _evaluation_metadata() -> dict[str, Any]:
    return {
        "metric_status": METRIC_STATUS,
        "metric_version": METRIC_VERSION,
        "is_experimental": True,
        "auto_tuning_eligible": False,
        "warning": FORWARD_EVALUATION_WARNING,
    }


def build_recommendation_accuracy(
    *,
    limit_reports: int = 30,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    fetch_nav: NavFetcher = fetch_fund_nav_history,
    trade_dates: frozenset[str] | None = None,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
    persist_outcomes: bool = False,
) -> dict[str, Any]:
    normalized_horizons = normalize_horizons(horizons)
    input_reports = list_reports()[: max(int(limit_reports), 0)]
    reports, deduplication = deduplicate_reports_by_calendar_date(input_reports)

    if not reports:
        empty_by_horizon = _empty_horizon_stats(normalized_horizons)
        return {
            **_evaluation_metadata(),
            "has_enough_data": False,
            "message": "暂无可评价的历史日报。",
            "report_count": 0,
            "selected_report_count": 0,
            "paired_days": 0,
            "horizons": list(normalized_horizons),
            "eligible_count": 0,
            "mature_count": 0,
            "skipped_count": 0,
            "immature_count": 0,
            "data_unavailable_count": 0,
            "coverage_percent": None,
            "deduplication": deduplication,
            "by_horizon": empty_by_horizon,
            "metric_contract_version": METRIC_CONTRACT_VERSION,
            "metrics": empty_by_horizon[f"T+{normalized_horizons[0]}"]["metrics"],
            "by_style": {},
            "summary_lines": [],
            "legacy_reference": {
                "excluded_from_formal_v2": True,
                "reason": "missing_audited_persisted_decision_event_v2",
                "report_count": 0,
                "recommendation_count": 0,
                "eligible_count": 0,
                "observation_count": 0,
                "mature_count": 0,
                "coverage_percent": None,
                "metrics": empty_by_horizon[
                    f"T+{normalized_horizons[0]}"
                ]["metrics"],
                "by_horizon": empty_by_horizon,
                "by_style": {},
                "summary_lines": [],
            },
        }

    resolved_trade_dates = trade_dates if trade_dates is not None else get_trade_date_set()

    # Fetch each fund once at the longest required tail across the selected report
    # window. This avoids N reports x M horizons network amplification.
    pull_days = max(recommended_nav_pull_days(report, normalized_horizons) for report in reports)
    nav_cache: dict[str, dict[str, Any] | None] = {}

    def cached_fetch(code: str, **_kwargs: Any) -> dict[str, Any] | None:
        if code not in nav_cache:
            try:
                nav_cache[code] = fetch_nav(code, trading_days=pull_days)
            except Exception:
                nav_cache[code] = None
        return nav_cache[code]

    buckets: dict[str, dict[str, Any]] = {}
    legacy_buckets: dict[str, dict[str, Any]] = {}
    for report in reports:
        style = _decision_style(report)
        formal_outcome = build_recommendation_outcomes(
            report,
            None,
            horizons=normalized_horizons,
            fetch_nav=cached_fetch,
            trade_dates=resolved_trade_dates,
            fetch_benchmark=fetch_benchmark,
            formal_v2_only=True,
        )
        if persist_outcomes:
            from app.services.decision_outcome_persistence import (
                persist_daily_outcome_result,
            )

            persist_daily_outcome_result(report, formal_outcome)
        if int(formal_outcome.get("recommendation_count") or 0) > 0:
            bucket = buckets.setdefault(style, _new_bucket(style, normalized_horizons))
            _accumulate_bucket(bucket, formal_outcome, normalized_horizons)

        legacy_outcome = build_recommendation_outcomes(
            report,
            None,
            horizons=normalized_horizons,
            fetch_nav=cached_fetch,
            trade_dates=resolved_trade_dates,
            fetch_benchmark=fetch_benchmark,
            legacy_reference_only=True,
        )
        if int(legacy_outcome.get("recommendation_count") or 0) > 0:
            legacy_bucket = legacy_buckets.setdefault(
                style, _new_bucket(style, normalized_horizons)
            )
            _accumulate_bucket(legacy_bucket, legacy_outcome, normalized_horizons)

    for bucket in buckets.values():
        _finalize_bucket(bucket, normalized_horizons)
    for bucket in legacy_buckets.values():
        _finalize_bucket(bucket, normalized_horizons)

    overall = _aggregate_horizons(buckets, normalized_horizons)
    legacy_overall = _aggregate_horizons(legacy_buckets, normalized_horizons)
    primary = overall[f"T+{normalized_horizons[0]}"]
    legacy_primary = legacy_overall[f"T+{normalized_horizons[0]}"]
    has_any_mature_sample = any(
        int(stats.get("mature_count") or 0) > 0 for stats in overall.values()
    )
    recommendation_count = sum(int(bucket["recommendation_count"]) for bucket in buckets.values())
    observation_count = sum(int(bucket["observation_count"]) for bucket in buckets.values())
    invalid_count = sum(int(bucket["invalid_count"]) for bucket in buckets.values())
    legacy_recommendation_count = sum(
        int(bucket["recommendation_count"]) for bucket in legacy_buckets.values()
    )
    legacy_observation_count = sum(
        int(bucket["observation_count"]) for bucket in legacy_buckets.values()
    )
    formal_report_count = sum(int(bucket["report_count"]) for bucket in buckets.values())
    legacy_report_count = sum(
        int(bucket["report_count"]) for bucket in legacy_buckets.values()
    )

    return {
        **_evaluation_metadata(),
        "has_enough_data": has_any_mature_sample,
        "message": (
            None
            if has_any_mature_sample
            else (
                "旧动态报告仍可作为历史参考，但不进入正式 V2 统计。"
                if legacy_recommendation_count
                else "已有正式 V2 方向建议，但所选 T+N 暂无成熟净值样本。"
            )
        ),
        "report_count": len(input_reports),
        "selected_report_count": len(reports),
        "formal_v2_report_count": formal_report_count,
        # Deprecated compatibility field. It now counts selected report dates,
        # never adjacent report pairs.
        "paired_days": formal_report_count,
        "horizons": list(normalized_horizons),
        "recommendation_count": recommendation_count,
        "eligible_count": primary["eligible_count"],
        "observation_count": observation_count,
        "invalid_count": invalid_count,
        "mature_count": primary["mature_count"],
        "skipped_count": primary["skipped_count"],
        "coverage_percent": primary["coverage_percent"],
        "deduplication": deduplication,
        "by_horizon": overall,
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "metrics": primary.get("metrics") or {},
        "by_style": buckets,
        "summary_lines": _summary_lines(buckets, normalized_horizons),
        "legacy_reference": {
            "excluded_from_formal_v2": True,
            "reason": "missing_audited_persisted_decision_event_v2",
            "report_count": legacy_report_count,
            "recommendation_count": legacy_recommendation_count,
            "eligible_count": legacy_primary["eligible_count"],
            "observation_count": legacy_observation_count,
            "mature_count": legacy_primary["mature_count"],
            "coverage_percent": legacy_primary["coverage_percent"],
            "metrics": legacy_primary.get("metrics") or {},
            "by_horizon": legacy_overall,
            "by_style": legacy_buckets,
            "summary_lines": _summary_lines(legacy_buckets, normalized_horizons),
        },
        "nav_fetch": {
            "unique_fund_count": len(nav_cache),
            "requested_trading_days": pull_days,
        },
    }


def _new_bucket(style: str, horizons: tuple[int, ...]) -> dict[str, Any]:
    return {
        "decision_style": style,
        "report_count": 0,
        "paired_count": 0,
        "recommendation_count": 0,
        "eligible_count": 0,
        "observation_count": 0,
        "invalid_count": 0,
        "by_horizon": _empty_horizon_stats(horizons),
        "items": [],
        "reversal": {
            "up_then_down_count": 0,
            "up_then_down_conservative_aligned": 0,
            "up_then_down_aggressive_miss": 0,
            "aggressive_miss_rate_percent": None,
            "metric_status": "retired_adjacent_report_heuristic",
        },
    }


def _empty_horizon_stats(horizons: tuple[int, ...]) -> dict[str, dict[str, Any]]:
    return {
        f"T+{horizon}": {
            "horizon_trading_days": horizon,
            "eligible_count": 0,
            "mature_count": 0,
            "skipped_count": 0,
            "immature_count": 0,
            "data_unavailable_count": 0,
            "hit_count": 0,
            "miss_count": 0,
            "hit_rate_percent": None,
            "coverage_percent": None,
            "metric_contract_version": METRIC_CONTRACT_VERSION,
            "metrics": _empty_metric_stats(),
        }
        for horizon in horizons
    }


def _accumulate_bucket(
    bucket: dict[str, Any],
    outcome: dict[str, Any],
    horizons: tuple[int, ...],
) -> None:
    bucket["report_count"] += 1
    bucket["paired_count"] += 1
    for field in ("recommendation_count", "eligible_count", "observation_count", "invalid_count"):
        bucket[field] += int(outcome.get(field) or 0)

    for horizon in horizons:
        key = f"T+{horizon}"
        source = (outcome.get("by_horizon") or {}).get(key) or {}
        target = bucket["by_horizon"][key]
        for field in (
            "eligible_count",
            "mature_count",
            "skipped_count",
            "immature_count",
            "data_unavailable_count",
            "hit_count",
            "miss_count",
        ):
            target[field] += int(source.get(field) or 0)
        _accumulate_metric_stats(target["metrics"], source.get("metrics") or {})

    if len(bucket["items"]) >= 8:
        return
    primary_key = f"T+{horizons[0]}"
    for item in outcome.get("items") or []:
        if len(bucket["items"]) >= 8:
            break
        primary = (item.get("by_horizon") or {}).get(primary_key) or {}
        bucket["items"].append(
            {
                "fund_code": item.get("fund_code"),
                "fund_name": item.get("fund_name"),
                # Compatibility name; this is the evaluated report action, not an
                # adjacent report's action.
                "previous_action": item.get("action"),
                "action": item.get("action"),
                "evaluation_class": item.get("evaluation_class"),
                "evaluation_status": primary.get("status"),
                "return_percent": primary.get("return_percent"),
                "direction_hit": primary.get("direction_hit"),
                "assessment": item.get("assessment"),
                "reversal_scenario": None,
            }
        )


def _finalize_bucket(bucket: dict[str, Any], horizons: tuple[int, ...]) -> None:
    for horizon in horizons:
        stats = bucket["by_horizon"][f"T+{horizon}"]
        _finalize_stats(stats)
        _finalize_metric_stats(stats["metrics"])
        for name in METRIC_NAMES:
            stats[name] = stats["metrics"][name]

    primary = bucket["by_horizon"][f"T+{horizons[0]}"]
    # Legacy flat keys remain bounded and now use recommendation-level T+1 data.
    bucket["hit_count"] = primary["hit_count"]
    bucket["miss_count"] = primary["miss_count"]
    bucket["hit_rate_percent"] = primary["hit_rate_percent"]
    bucket["mature_count"] = primary["mature_count"]
    bucket["skipped_count"] = primary["skipped_count"]
    bucket["coverage_percent"] = primary["coverage_percent"]


def _aggregate_horizons(
    buckets: dict[str, dict[str, Any]],
    horizons: tuple[int, ...],
) -> dict[str, dict[str, Any]]:
    overall = _empty_horizon_stats(horizons)
    for horizon in horizons:
        key = f"T+{horizon}"
        target = overall[key]
        for bucket in buckets.values():
            source = bucket["by_horizon"][key]
            for field in (
                "eligible_count",
                "mature_count",
                "skipped_count",
                "immature_count",
                "data_unavailable_count",
                "hit_count",
                "miss_count",
            ):
                target[field] += int(source.get(field) or 0)
            _accumulate_metric_stats(target["metrics"], source.get("metrics") or {})
        _finalize_stats(target)
        _finalize_metric_stats(target["metrics"])
        for name in METRIC_NAMES:
            target[name] = target["metrics"][name]
    return overall


def _finalize_stats(stats: dict[str, Any]) -> None:
    mature = int(stats.get("mature_count") or 0)
    eligible = int(stats.get("eligible_count") or 0)
    hits = int(stats.get("hit_count") or 0)
    rate = round(hits / mature * 100.0, 1) if mature else None
    coverage = round(mature / eligible * 100.0, 1) if eligible else None
    stats["hit_rate_percent"] = min(rate, 100.0) if rate is not None else None
    stats["coverage_percent"] = min(coverage, 100.0) if coverage is not None else None


def _empty_metric_stats() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "eligible_count": 0,
            "mature_count": 0,
            "unavailable_count": 0,
            "hit_count": 0,
            "miss_count": 0,
            "coverage_percent": None,
            "hit_rate_percent": None,
        }
        for name in METRIC_NAMES
    }


def _accumulate_metric_stats(
    target: dict[str, dict[str, Any]],
    source: dict[str, Any],
) -> None:
    for name in METRIC_NAMES:
        source_metric = source.get(name) or {}
        target_metric = target[name]
        for field in (
            "eligible_count",
            "mature_count",
            "unavailable_count",
            "hit_count",
            "miss_count",
        ):
            target_metric[field] += int(source_metric.get(field) or 0)


def _finalize_metric_stats(metrics: dict[str, dict[str, Any]]) -> None:
    for value in metrics.values():
        eligible = int(value.get("eligible_count") or 0)
        mature = int(value.get("mature_count") or 0)
        hits = int(value.get("hit_count") or 0)
        value["unavailable_count"] = max(eligible - mature, 0)
        value["coverage_percent"] = (
            round(mature / eligible * 100.0, 1) if eligible else None
        )
        value["hit_rate_percent"] = round(hits / mature * 100.0, 1) if mature else None


def _decision_style(report: dict[str, Any]) -> str:
    facts = report.get("analysis_facts") or {}
    portfolio = facts.get("portfolio") or {}
    style = portfolio.get("decision_style")
    if style in {"tactical", "conservative", "aggressive"}:
        return style
    profile = report.get("profile") or {}
    if profile.get("decision_style") in {"tactical", "conservative", "aggressive"}:
        return str(profile["decision_style"])
    return "conservative"


def _summary_lines(
    buckets: dict[str, dict[str, Any]],
    horizons: tuple[int, ...],
) -> list[str]:
    labels = {"tactical": "战术短线", "aggressive": "激进波段", "conservative": "稳健"}
    lines: list[str] = []
    primary_key = f"T+{horizons[0]}"
    for style, bucket in buckets.items():
        stats = bucket["by_horizon"][primary_key]
        rate = stats.get("hit_rate_percent")
        rate_text = f"，方向命中率 {rate}%" if rate is not None else ""
        lines.append(
            f"{labels.get(style, style)}：{primary_key} 成熟 {stats['mature_count']}/"
            f"{stats['eligible_count']} 条（覆盖率 {stats['coverage_percent']}%）{rate_text}；"
            f"观察/复核类 {bucket['observation_count']} 条单列。"
        )
    return lines
