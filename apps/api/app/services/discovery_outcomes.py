from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from app.services.akshare_subprocess import fetch_fund_nav_history
from app.services.benchmark_fee_evaluation import (
    METRIC_CONTRACT_VERSION,
    BenchmarkFetcher,
    default_benchmark_fetcher,
    evaluate_decision_metrics,
    evaluate_frozen_benchmark,
    fee_policy_from_report,
    find_frozen_decision_event,
    is_formal_v2_metric_event,
    metric_aliases,
    summarize_metrics,
)
from app.services.fund_factor_nav import build_total_return_index
from app.services.outcome_path_metrics import (
    build_path_metrics,
    build_strategy_evaluation_policy,
    evaluate_no_action_counterfactual,
    summarize_no_action_counterfactuals,
    summarize_path_metrics,
    unavailable_path_metrics,
)
from app.services.selection_baseline_evaluation import (
    evaluate_candidate_baselines,
    summarize_candidate_baselines,
)

_CN_TZ = ZoneInfo("Asia/Shanghai")
_MARKET_CLOSE = time(15, 0)
_SUPPORTED_HORIZONS = (5, 20, 60)
_MAX_HORIZON_DAYS = 90
_TAKE_PROFIT_DAYS = 3
_BENCHMARK_UNAVAILABLE_REASON = "point_in_time_fund_benchmark_mapping_unavailable"
_BUY_ACTIONS = {"分批买入", "少量买入"}
_WATCH_ACTIONS = {"建议关注", "观察", "继续观察", "加入观察"}
_CONDITIONAL_ACTIONS = {"等待回调"}
_EVENT_SCHEMA_VERSION = "1.0"
_NAV_OBSERVATION_SOURCE = "akshare.fund_open_fund_info_em"


def _memoized_nav_fetcher(fetch_nav, *, trading_days_override: int | None = None):
    cache: dict[tuple[str, int], object] = {}

    def fetch(code: str, *, trading_days: int):
        effective_days = (
            trading_days_override
            if trading_days_override is not None
            else trading_days
        )
        normalized_code = _normalize_fund_code(code) or str(code).strip()
        key = (normalized_code, effective_days)
        if key not in cache:
            cache[key] = fetch_nav(code, trading_days=effective_days)
        return cache[key]

    return fetch


def build_discovery_outcomes(
    report: dict[str, Any],
    *,
    days: int = 7,
    fetch_nav=fetch_fund_nav_history,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
) -> dict[str, Any]:
    horizon_days = _normalize_horizon_days(days)
    fetch_nav = _memoized_nav_fetcher(fetch_nav)
    recommendations = report.get("recommendations") or []
    if not recommendations:
        return _build_outcome_response(
            report=report,
            days=horizon_days,
            items=[],
            message="该报告无推荐条目，无法复盘。",
        )

    created_at = _parse_datetime(report.get("created_at"))
    if created_at is None:
        items: list[dict[str, Any]] = []
        fallback_fee_policy = fee_policy_from_report(report, decision_kind="discovery")
        for recommendation_index, rec in enumerate(recommendations):
            if not isinstance(rec, dict):
                continue
            code = _normalize_fund_code(rec.get("fund_code"))
            frozen_event = find_frozen_decision_event(
                report,
                recommendation_index=recommendation_index,
                fund_code=code,
            )
            fee_policy = (
                dict(
                    frozen_event.get("fee_policy")
                    or frozen_event.get("fee_model")
                    or {}
                )
                if frozen_event is not None
                else fallback_fee_policy
            )
            item = _skipped_item(
                rec,
                days=horizon_days,
                skip_reason="report_created_at_invalid",
                assessment="报告时间解析失败，该条推荐不进入命中率。",
            )
            _attach_item_contract(
                item,
                report=report,
                recommendation_index=recommendation_index,
                frozen_event=frozen_event,
                fee_policy=fee_policy,
                benchmark_spec=dict((frozen_event or {}).get("benchmark") or {}),
            )
            items.append(item)
        return _build_outcome_response(
            report=report,
            days=horizon_days,
            items=items,
            message="报告时间解析失败，所有推荐均已单列跳过。",
        )

    items: list[dict[str, Any]] = []
    take_profit_threshold = _resolve_take_profit_threshold(report)
    fallback_fee_policy = fee_policy_from_report(report, decision_kind="discovery")
    for recommendation_index, rec in enumerate(recommendations):
        if not isinstance(rec, dict):
            continue
        action = str(rec.get("action") or "").strip()
        action_category = _classify_action(action)
        code = _normalize_fund_code(rec.get("fund_code"))
        frozen_event = find_frozen_decision_event(
            report,
            recommendation_index=recommendation_index,
            fund_code=code,
        )
        fee_policy = (
            dict(frozen_event.get("fee_policy") or frozen_event.get("fee_model") or {})
            if frozen_event is not None
            else fallback_fee_policy
        )
        benchmark_spec = dict((frozen_event or {}).get("benchmark") or {})
        if action_category != "buy":
            entry_trigger = rec.get("entry_trigger")
            has_entry_trigger = bool(
                isinstance(entry_trigger, dict) and entry_trigger.get("conditions")
            )
            reason_by_category = {
                "watch_only": "watch_only_action",
                "conditional_wait": (
                    "conditional_action_pending_entry_trigger"
                    if has_entry_trigger
                    else "conditional_action_without_entry_trigger"
                ),
                "unknown": "unknown_action",
            }
            item = _skipped_item(
                    rec,
                    days=horizon_days,
                    skip_reason=reason_by_category[action_category],
                    assessment=_ineligible_assessment(
                        action_category,
                        action,
                        has_entry_trigger=has_entry_trigger,
                    ),
                )
            _attach_item_contract(
                item,
                report=report,
                recommendation_index=recommendation_index,
                frozen_event=frozen_event,
                fee_policy=fee_policy,
                benchmark_spec=benchmark_spec,
            )
            items.append(item)
            continue

        if code is None:
            item = _skipped_item(
                    rec,
                    days=horizon_days,
                    skip_reason="invalid_fund_code",
                    assessment="基金代码无效，该买入建议无法匹配净值并已跳过。",
                )
            _attach_item_contract(
                item,
                report=report,
                recommendation_index=recommendation_index,
                frozen_event=frozen_event,
                fee_policy=fee_policy,
                benchmark_spec=benchmark_spec,
            )
            items.append(item)
            continue

        outcome = _outcome_for_fund(
            code=code,
            fund_name=str(rec.get("fund_name", "")),
            action=action,
            since=created_at,
            days=horizon_days,
            fetch_nav=fetch_nav,
            take_profit_threshold_percent=take_profit_threshold,
            take_profit_days=_TAKE_PROFIT_DAYS,
            fee_policy=fee_policy,
            benchmark_spec=benchmark_spec,
            benchmark_is_frozen=frozen_event is not None,
            fetch_benchmark=fetch_benchmark,
            recommendation=rec,
            baseline_comparators=dict(
                (frozen_event or {}).get("baseline_comparators") or {}
            ),
        )
        _attach_item_contract(
            outcome,
            report=report,
            recommendation_index=recommendation_index,
            frozen_event=frozen_event,
            fee_policy=fee_policy,
            benchmark_spec=benchmark_spec,
        )
        items.append(outcome)

    return _build_outcome_response(report=report, days=horizon_days, items=items)


def build_discovery_recommendation_accuracy(
    reports: list[dict[str, Any]],
    *,
    days: int = 30,
    fetch_nav=fetch_fund_nav_history,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
    persist_outcomes: bool = False,
) -> dict[str, Any]:
    horizon_days = _normalize_horizon_days(days)
    if not reports:
        empty_metrics = summarize_metrics([])
        return {
            "days": horizon_days,
            "horizon": f"T+{horizon_days}",
            "supported_horizons": list(_SUPPORTED_HORIZONS),
            "sample_count": 0,
            "eligible_count": 0,
            "mature_count": 0,
            "pending_count": 0,
            "skipped_count": 0,
            "coverage_percent": None,
            "hit_count": 0,
            "hit_rate_percent": None,
            "benchmark": _benchmark_unavailable(),
            "metric_contract_version": METRIC_CONTRACT_VERSION,
            "metrics": empty_metrics,
            "formal_v2_report_count": 0,
            "legacy_reference": _empty_discovery_legacy_reference(
                horizon_days,
                empty_metrics,
            ),
            "message": "暂无推荐报告样本。",
        }

    formal_items: list[dict[str, Any]] = []
    legacy_items: list[dict[str, Any]] = []
    formal_report_ids: set[str] = set()
    legacy_report_ids: set[str] = set()
    max_pull_days = max(
        (
            _nav_pull_days(_resolve_executable_date(created_at), horizon_days)
            for report in reports
            if (created_at := _parse_datetime(report.get("created_at"))) is not None
        ),
        default=None,
    )
    shared_fetch_nav = _memoized_nav_fetcher(
        fetch_nav,
        trading_days_override=max_pull_days,
    )
    for report in reports:
        outcome = build_discovery_outcomes(
            report,
            days=horizon_days,
            fetch_nav=shared_fetch_nav,
            fetch_benchmark=fetch_benchmark,
        )
        if persist_outcomes:
            from app.services.decision_outcome_persistence import (
                persist_discovery_outcome_result,
            )

            persist_discovery_outcome_result(report, outcome)
        for item in outcome.get("items", []):
            if not isinstance(item, dict):
                continue
            report_id = str(report.get("id") or "legacy-unknown")
            if is_formal_v2_metric_event(report, item.get("decision_event")):
                formal_items.append(item)
                formal_report_ids.add(report_id)
            else:
                legacy_items.append(item)
                legacy_report_ids.add(report_id)

    formal = _summarize_discovery_accuracy_items(formal_items)
    legacy = _summarize_discovery_accuracy_items(legacy_items)
    metric_summary = formal["metrics"]
    return {
        "days": horizon_days,
        "horizon": f"T+{horizon_days}",
        "supported_horizons": list(_SUPPORTED_HORIZONS),
        "sample_count": formal["mature_count"],
        "eligible_count": formal["eligible_count"],
        "mature_count": formal["mature_count"],
        "pending_count": formal["pending_count"],
        "skipped_count": formal["skipped_count"],
        "coverage_percent": formal["coverage_percent"],
        "hit_count": formal["hit_count"],
        "hit_rate_percent": formal["hit_rate_percent"],
        "formal_v2_report_count": len(formal_report_ids),
        "hit_definition": "positive_absolute_return_before_costs",
        "benchmark": _benchmark_unavailable(),
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "metrics": metric_summary,
        "gross_direction": metric_summary["gross_direction"],
        "positive_net_return": metric_summary["positive_net_return"],
        "gross_excess": metric_summary["gross_excess"],
        "net_excess": metric_summary["net_excess"],
        "legacy_reference": {
            "excluded_from_formal_v2": True,
            "reason": "missing_audited_persisted_decision_event_v2",
            "report_count": len(legacy_report_ids),
            **legacy,
        },
        "message": (
            f"近 {len(reports)} 份报告中，正式 V2 有 {formal['eligible_count']} 条买入动作，"
            f"{formal['mature_count']} 条已达到 T+{horizon_days}"
            f"（覆盖率 {formal['coverage_percent']}%），方向命中率 {formal['hit_rate_percent']}%。"
            if formal["hit_rate_percent"] is not None
            else (
                "旧动态荐基报告仍可作为历史参考，但不进入正式 V2 统计。"
                if legacy_items
                else "尚无达到目标估值日的正式 V2 买入样本。"
            )
        ),
    }


def _summarize_discovery_accuracy_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = sum(1 for item in items if item.get("eligible"))
    mature = sum(1 for item in items if item.get("eligible") and item.get("mature"))
    pending = sum(
        1
        for item in items
        if item.get("eligible") and not item.get("mature") and not item.get("skipped")
    )
    skipped = sum(1 for item in items if item.get("skipped"))
    hits = sum(
        1
        for item in items
        if item.get("eligible")
        and item.get("mature")
        and item.get("direction_aligned") is True
    )
    metrics = summarize_metrics(item.get("metrics") for item in items)
    return {
        "total_count": len(items),
        "eligible_count": eligible,
        "mature_count": mature,
        "pending_count": pending,
        "skipped_count": skipped,
        "coverage_percent": round(mature / eligible * 100, 1) if eligible else None,
        "hit_count": hits,
        "hit_rate_percent": round(hits / mature * 100, 1) if mature else None,
        "metrics": metrics,
    }


def _empty_discovery_legacy_reference(
    horizon_days: int,
    empty_metrics: dict[str, Any],
) -> dict[str, Any]:
    _ = horizon_days
    return {
        "excluded_from_formal_v2": True,
        "reason": "missing_audited_persisted_decision_event_v2",
        "report_count": 0,
        "total_count": 0,
        "eligible_count": 0,
        "mature_count": 0,
        "pending_count": 0,
        "skipped_count": 0,
        "coverage_percent": None,
        "hit_count": 0,
        "hit_rate_percent": None,
        "metrics": empty_metrics,
    }


def _outcome_for_fund(
    *,
    code: str,
    fund_name: str,
    action: str,
    since: datetime,
    days: int,
    fetch_nav,
    take_profit_threshold_percent: float | None = None,
    take_profit_days: int = _TAKE_PROFIT_DAYS,
    fee_policy: dict[str, Any] | None = None,
    benchmark_spec: dict[str, Any] | None = None,
    benchmark_is_frozen: bool = False,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
    recommendation: dict[str, Any] | None = None,
    baseline_comparators: dict[str, Any] | None = None,
) -> dict[str, Any]:
    executable_date = _resolve_executable_date(since)
    pull_days = _nav_pull_days(executable_date, days)
    payload = fetch_nav(code, trading_days=pull_days)
    rows = None
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("rows")
    if not rows:
        return _data_skipped_outcome(
            code=code,
            fund_name=fund_name,
            action=action,
            days=days,
            executable_date=executable_date,
            reason="nav_history_unavailable",
            assessment="净值历史暂不可用，该买入建议不进入命中率。",
        )

    unit_nav_points = _normalize_nav_points(rows)
    unit_nav_by_date = dict(unit_nav_points)
    total_return_series = build_total_return_index(rows)
    points = total_return_series.points
    baseline_index = next(
        (index for index, (day, _nav) in enumerate(points) if day >= executable_date),
        None,
    )
    if baseline_index is None:
        return _data_skipped_outcome(
            code=code,
            fund_name=fund_name,
            action=action,
            days=days,
            executable_date=executable_date,
            reason="baseline_nav_unavailable",
            assessment="执行日起无可用基线净值，该买入建议不进入命中率。",
        )

    baseline_date, baseline_return_index = points[baseline_index]
    baseline_nav = unit_nav_by_date.get(baseline_date)
    forward_points = points[baseline_index + 1 :]
    observed_forward = len(forward_points)
    latest_observed = (
        forward_points[-1]
        if forward_points
        else (baseline_date, baseline_return_index)
    )
    latest_observed_nav = unit_nav_by_date.get(latest_observed[0])
    common = {
        "fund_code": code,
        "fund_name": fund_name,
        "action": action,
        "action_category": "buy",
        "eligible": True,
        "horizon_trading_days": days,
        "baseline_policy": "first_valuation_on_or_after_executable_date",
        "executable_date": executable_date,
        "baseline_nav_date": baseline_date,
        "baseline_nav": round(baseline_nav, 4) if baseline_nav is not None else None,
        "baseline_total_return_index": round(baseline_return_index, 8),
        "observed_forward_trading_days": observed_forward,
        "latest_observed_nav": (
            round(latest_observed_nav, 4)
            if latest_observed_nav is not None
            else None
        ),
        "latest_observed_nav_date": latest_observed[0],
        "maturity_basis": "fund_nav_valuation_dates",
        "fee_policy": dict(fee_policy or {}),
        "return_series": {
            "basis": "total_return_daily_growth_first",
            "point_count": len(points),
            "daily_growth_points": total_return_series.daily_return_points,
            "nav_ratio_fallback_points": total_return_series.nav_ratio_points,
            "invalid_points": total_return_series.invalid_points,
        },
        "baseline_comparators": dict(baseline_comparators or {}),
        "benchmark": {
            "tier": str((benchmark_spec or {}).get("tier") or "unavailable"),
            "available": False,
            "formal_excess_eligible": False,
            "return_percent": None,
            "reference_return_percent": None,
            "reason": str(
                (benchmark_spec or {}).get("reason")
                or "point_in_time_benchmark_not_frozen"
            ),
        },
        **_item_benchmark_unavailable(),
    }

    if observed_forward < days:
        partial_change = round(
            (latest_observed[1] / baseline_return_index - 1) * 100,
            2,
        )
        metrics = evaluate_decision_metrics(
            gross_return_percent=None,
            evaluation_class="buy",
            fee_policy=fee_policy,
            benchmark_result=common["benchmark"],
        )
        return {
            **common,
            "mature": False,
            "skipped": False,
            "skip_reason": None,
            "status": "pending",
            "target_nav_date": None,
            "target_nav": None,
            "latest_nav": (
                round(latest_observed_nav, 4)
                if latest_observed_nav is not None
                else None
            ),
            "latest_nav_date": latest_observed[0],
            "period_change_percent": None,
            "partial_change_percent": partial_change,
            "direction_aligned": None,
            "metrics": metrics,
            "path_metrics": unavailable_path_metrics("target_total_return_unavailable"),
            "no_action_counterfactual": evaluate_no_action_counterfactual(
                gross_return_percent=None,
                evaluation_class="buy",
                recommendation=recommendation,
                fee_policy=fee_policy,
            ),
            "selection_baseline_results": {
                "status": "pending",
                "horizon_trading_days": days,
                "comparators": dict(baseline_comparators or {}),
            },
            **metric_aliases(metrics),
            "assessment": (
                f"尚未达到 T+{days}：当前仅有 {observed_forward} 个后续估值日，"
                "暂不判定命中。"
            ),
            "hit_take_profit_within_days": _hit_take_profit_from_points(
                points,
                baseline_index=baseline_index,
                forward_trading_days=take_profit_days,
                threshold_percent=take_profit_threshold_percent,
            ),
        }

    target_index = baseline_index + days
    target_date, target_return_index = points[target_index]
    target_nav = unit_nav_by_date.get(target_date)
    change = round(
        (target_return_index / baseline_return_index - 1) * 100,
        2,
    )
    benchmark_result = evaluate_frozen_benchmark(
        benchmark_spec,
        baseline_date=baseline_date,
        target_date=target_date,
        is_frozen=benchmark_is_frozen,
        fetch_component=fetch_benchmark,
    )
    metrics = evaluate_decision_metrics(
        gross_return_percent=change,
        evaluation_class="buy",
        fee_policy=fee_policy,
        benchmark_result=benchmark_result,
    )
    path_metrics = build_path_metrics(
        points,
        baseline_index=baseline_index,
        target_index=target_index,
    )
    no_action_counterfactual = evaluate_no_action_counterfactual(
        gross_return_percent=change,
        evaluation_class="buy",
        recommendation=recommendation,
        fee_policy=fee_policy,
    )
    selection_baseline_results = evaluate_candidate_baselines(
        baseline_comparators,
        execution_date=executable_date,
        horizon=days,
        target_net_return_percent=metrics["positive_net_return"].get("value_percent"),
        fetch_nav=fetch_nav,
        trading_days=pull_days,
        fee_policy=fee_policy,
    )
    aligned = bool(metrics["gross_direction"]["hit"])
    return {
        **common,
        "benchmark": benchmark_result,
        "mature": True,
        "skipped": False,
        "skip_reason": None,
        "status": "hit" if aligned else "miss",
        "target_nav_date": target_date,
        "target_nav": round(target_nav, 4) if target_nav is not None else None,
        "target_total_return_index": round(target_return_index, 8),
        # 兼容旧字段，但语义固定为 T+N 目标点，不再指向数据集最后一条。
        "latest_nav": round(target_nav, 4),
        "latest_nav_date": target_date,
        "period_change_percent": change,
        "partial_change_percent": None,
        "direction_aligned": aligned,
        **metric_aliases(metrics),
        "metrics": metrics,
        "path_metrics": path_metrics,
        "no_action_counterfactual": no_action_counterfactual,
        "selection_baseline_results": selection_baseline_results,
        "assessment": _assessment_label(
            action,
            change,
            aligned,
            days=days,
            metrics=metrics,
        ),
        "hit_take_profit_within_days": _hit_take_profit_from_points(
            points,
            baseline_index=baseline_index,
            forward_trading_days=take_profit_days,
            threshold_percent=take_profit_threshold_percent,
        ),
    }


def _hit_take_profit_from_points(
    points: list[tuple[str, float]],
    *,
    baseline_index: int,
    forward_trading_days: int,
    threshold_percent: float | None,
) -> bool | None:
    threshold = _as_float(threshold_percent)
    if threshold is None or threshold < 0 or forward_trading_days <= 0:
        return None
    end_index = baseline_index + forward_trading_days + 1
    if baseline_index < 0 or end_index > len(points):
        return None
    baseline_nav = points[baseline_index][1]
    target_nav = baseline_nav * (1.0 + threshold / 100.0)
    return any(nav >= target_nav for _day, nav in points[baseline_index + 1 : end_index])


def _attach_item_contract(
    item: dict[str, Any],
    *,
    report: dict[str, Any],
    recommendation_index: int,
    frozen_event: dict[str, Any] | None,
    fee_policy: dict[str, Any],
    benchmark_spec: dict[str, Any],
) -> None:
    item["recommendation_index"] = recommendation_index
    item["fee_policy"] = dict(fee_policy)
    item.setdefault(
        "benchmark",
        {
            "tier": str(benchmark_spec.get("tier") or "unavailable"),
            "available": False,
            "formal_excess_eligible": False,
            "return_percent": None,
            "reference_return_percent": None,
            "reason": str(
                benchmark_spec.get("reason")
                or "point_in_time_benchmark_not_frozen"
            ),
        },
    )
    if frozen_event is not None:
        item["decision_event"] = dict(frozen_event)
        item["event_id"] = str(frozen_event.get("event_id") or "") or None
    item.setdefault(
        "metrics",
        evaluate_decision_metrics(
            gross_return_percent=item.get("period_change_percent"),
            evaluation_class=str(item.get("action_category") or ""),
            fee_policy=fee_policy,
            benchmark_result=item.get("benchmark"),
        ),
    )
    item.update(metric_aliases(item["metrics"]))
    item.setdefault(
        "path_metrics",
        unavailable_path_metrics(str(item.get("skip_reason") or "outcome_not_mature")),
    )
    item.setdefault(
        "no_action_counterfactual",
        evaluate_no_action_counterfactual(
            gross_return_percent=item.get("period_change_percent"),
            evaluation_class=str(item.get("action_category") or ""),
            recommendation=(frozen_event or {}).get("recommendation") or {},
            fee_policy=fee_policy,
        ),
    )
    item["metric_contract_version"] = METRIC_CONTRACT_VERSION


def _build_outcome_response(
    *,
    report: dict[str, Any],
    days: int,
    items: list[dict[str, Any]],
    message: str | None = None,
) -> dict[str, Any]:
    eligible = sum(1 for item in items if item.get("eligible"))
    mature = sum(1 for item in items if item.get("eligible") and item.get("mature"))
    pending = sum(
        1
        for item in items
        if item.get("eligible") and not item.get("mature") and not item.get("skipped")
    )
    skipped = sum(1 for item in items if item.get("skipped"))
    hits = sum(
        1
        for item in items
        if item.get("eligible")
        and item.get("mature")
        and item.get("direction_aligned") is True
    )
    coverage = round(mature / eligible * 100, 1) if eligible else None
    hit_rate = round(hits / mature * 100, 1) if mature else None
    metric_summary = summarize_metrics(item.get("metrics") for item in items)
    path_summary = summarize_path_metrics(
        item.get("path_metrics") for item in items if item.get("eligible")
    )
    no_action_summary = summarize_no_action_counterfactuals(
        item.get("no_action_counterfactual") for item in items if item.get("eligible")
    )
    selection_baseline_summary = summarize_candidate_baselines(
        item.get("selection_baseline_results")
        for item in items
        if item.get("eligible")
    )

    if message is None:
        if mature:
            net_stats = metric_summary["positive_net_return"]
            excess_stats = metric_summary["gross_excess"]
            message = (
                f"T+{days} 已成熟 {mature}/{eligible} 条可评价买入动作"
                f"（覆盖率 {coverage}%），其中 {hits}/{mature} 条取得正收益。"
                f"用户假设费后覆盖 {net_stats['mature_count']}/{net_stats['eligible_count']} 条，"
                f"正式基金基准超额覆盖 {excess_stats['mature_count']}/{excess_stats['eligible_count']} 条；"
                "缺失费率或基准不计命中也不计失败，不代表未来。"
            )
        elif eligible:
            message = (
                f"共有 {eligible} 条可评价买入动作，但尚无样本达到 T+{days}；"
                f"待成熟 {pending} 条，数据原因跳过 {skipped} 条，暂不计算命中率。"
            )
        elif items:
            message = "本报告仅含观察、等待或未知动作，已单列展示且不计入命中率。"
        else:
            message = "暂无可复盘条目。"

    decision_events, outcome_observations = _build_dynamic_event_contract(
        report=report,
        items=items,
        days=days,
    )
    return {
        "schema_version": _EVENT_SCHEMA_VERSION,
        "event_contract": {
            "decision_event_schema_version": (
                "decision_event.v2"
                if any(
                    str(event.get("schema_version") or "") == "decision_event.v2"
                    for event in decision_events
                )
                else _EVENT_SCHEMA_VERSION
            ),
            "outcome_observation_schema_version": (
                "outcome_observation.v2"
                if any(
                    str(event.get("schema_version") or "") == "decision_event.v2"
                    for event in decision_events
                )
                else _EVENT_SCHEMA_VERSION
            ),
            "persistence": (
                "persisted"
                if any(
                    str(event.get("schema_version") or "") == "decision_event.v2"
                    for event in decision_events
                )
                else "dynamic_not_persisted"
            ),
            "metric_contract_version": METRIC_CONTRACT_VERSION,
        },
        "strategy_evaluation_policy": build_strategy_evaluation_policy(
            decision_kind="discovery",
            report=report,
            facts=report.get("discovery_facts") or {},
        ),
        "has_data": mature > 0,
        "days": days,
        "horizon": f"T+{days}",
        "supported_horizons": list(_SUPPORTED_HORIZONS),
        "trading_day_basis": "fund_nav_valuation_dates",
        "baseline_policy": "first_valuation_on_or_after_executable_date",
        "hit_definition": "positive_absolute_return_before_costs",
        "total_count": len(items),
        "eligible_count": eligible,
        "mature_count": mature,
        "pending_count": pending,
        "skipped_count": skipped,
        "coverage_percent": coverage,
        "hit_count": hits,
        "hit_rate_percent": hit_rate,
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "metrics": metric_summary,
        "gross_direction": metric_summary["gross_direction"],
        "positive_net_return": metric_summary["positive_net_return"],
        "gross_excess": metric_summary["gross_excess"],
        "net_excess": metric_summary["net_excess"],
        "path_metrics": path_summary,
        "no_action_counterfactual": no_action_summary,
        "selection_baselines": selection_baseline_summary,
        "coverage": {
            "total": len(items),
            "eligible": eligible,
            "mature": mature,
            "pending": pending,
            "skipped": skipped,
            "mature_over_eligible_percent": coverage,
        },
        "benchmark": _benchmark_unavailable(),
        "decision_events": decision_events,
        "outcome_observations": outcome_observations,
        "message": message,
        "items": items,
    }


def _build_dynamic_event_contract(
    *,
    report: dict[str, Any],
    items: list[dict[str, Any]],
    days: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    report_id = _stable_report_id(report)
    parsed_decision_at = _parse_datetime(report.get("created_at"))
    decision_at = (
        parsed_decision_at.astimezone(timezone.utc).isoformat()
        if parsed_decision_at is not None
        else None
    )
    observation_at = _observation_now()
    if observation_at.tzinfo is None:
        observation_at = observation_at.replace(tzinfo=timezone.utc)
    observation_at_text = observation_at.astimezone(timezone.utc).isoformat()

    code_totals: dict[str, int] = {}
    for item in items:
        code = _event_fund_code(item)
        code_totals[code] = code_totals.get(code, 0) + 1

    duplicate_ordinals: dict[tuple[str, str], int] = {}
    decision_events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for item in items:
        persisted_event = item.get("decision_event")
        if (
            isinstance(persisted_event, dict)
            and str(persisted_event.get("schema_version") or "") == "decision_event.v2"
            and persisted_event.get("event_id")
        ):
            event_id = str(persisted_event["event_id"])
            observation_id = f"{event_id}:T+{days}"
            item["event_id"] = event_id
            item["observation_id"] = observation_id
            decision_events.append(dict(persisted_event))
            observations.append(
                {
                    "schema_version": "outcome_observation.v2",
                    "observation_id": observation_id,
                    "event_id": event_id,
                    "horizon_trading_days": days,
                    "target_date": item.get("target_nav_date"),
                    "observation_at": None,
                    "source_available_at": None,
                    "status": str(item.get("status") or "skipped"),
                    "source": (
                        _NAV_OBSERVATION_SOURCE
                        if item.get("action_category") == "buy"
                        else "not_applicable"
                    ),
                    "baseline": {
                        "date": item.get("baseline_nav_date"),
                        "nav": item.get("baseline_nav"),
                    },
                    "target": {
                        "date": item.get("target_nav_date"),
                        "nav": item.get("target_nav"),
                    },
                    "return_percent": item.get("period_change_percent"),
                    "direction_hit": item.get("direction_aligned"),
                    "eligible": bool(item.get("eligible")),
                    "mature": bool(item.get("mature")),
                    "skipped": bool(item.get("skipped")),
                    "skip_reason": item.get("skip_reason"),
                    "metrics": item.get("metrics") or {},
                    "path_metrics": item.get("path_metrics") or {},
                    "no_action_counterfactual": item.get("no_action_counterfactual") or {},
                    "selection_baseline_results": item.get("selection_baseline_results") or {},
                    "benchmark": item.get("benchmark"),
                    "fee_policy": item.get("fee_policy"),
                }
            )
            continue

        code = _event_fund_code(item)
        action = str(item.get("action") or "")
        event_id = f"discovery:{report_id}:{code}"
        if code_totals[code] > 1:
            action_hash = hashlib.sha256(action.encode("utf-8")).hexdigest()[:8]
            duplicate_key = (code, action_hash)
            ordinal = duplicate_ordinals.get(duplicate_key, 0) + 1
            duplicate_ordinals[duplicate_key] = ordinal
            event_id = f"{event_id}:{action_hash}:{ordinal}"

        observation_id = f"{event_id}:T+{days}"
        item["event_id"] = event_id
        item["observation_id"] = observation_id
        decision_events.append(
            {
                "schema_version": _EVENT_SCHEMA_VERSION,
                "event_id": event_id,
                "event_type": "fund_discovery_decision",
                "report_id": report_id,
                "decision_at": decision_at,
                "fund_code": str(item.get("fund_code") or ""),
                "fund_name": str(item.get("fund_name") or ""),
                "action": action,
                "action_category": str(item.get("action_category") or "unknown"),
                "eligible": bool(item.get("eligible")),
            }
        )
        observations.append(
            {
                "schema_version": _EVENT_SCHEMA_VERSION,
                "observation_id": observation_id,
                "event_id": event_id,
                "horizon_trading_days": days,
                "target_date": item.get("target_nav_date"),
                "observation_at": observation_at_text,
                "status": str(item.get("status") or "skipped"),
                "source": (
                    _NAV_OBSERVATION_SOURCE
                    if item.get("action_category") == "buy"
                    else "not_applicable"
                ),
                "baseline": {
                    "date": item.get("baseline_nav_date"),
                    "nav": item.get("baseline_nav"),
                },
                "target": {
                    "date": item.get("target_nav_date"),
                    "nav": item.get("target_nav"),
                },
                "return_percent": item.get("period_change_percent"),
                "direction_hit": item.get("direction_aligned"),
                "eligible": bool(item.get("eligible")),
                "mature": bool(item.get("mature")),
                "skipped": bool(item.get("skipped")),
                "skip_reason": item.get("skip_reason"),
                "metrics": item.get("metrics") or {},
                "path_metrics": item.get("path_metrics") or {},
                "no_action_counterfactual": item.get("no_action_counterfactual") or {},
                "selection_baseline_results": item.get("selection_baseline_results") or {},
                "benchmark": item.get("benchmark"),
                "fee_policy": item.get("fee_policy"),
            }
        )
    return decision_events, observations


def _stable_report_id(report: dict[str, Any]) -> str:
    report_id = str(report.get("id") or "").strip()
    if report_id:
        return report_id
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)
    return f"legacy-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:12]}"


def _event_fund_code(item: dict[str, Any]) -> str:
    code = str(item.get("fund_code") or "").strip()
    if code and all(character.isalnum() or character in "-_." for character in code):
        return code
    encoded = code or "missing"
    return f"unknown-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:8]}"


def _observation_now() -> datetime:
    return datetime.now(timezone.utc)


def _skipped_item(
    recommendation: dict[str, Any],
    *,
    days: int,
    skip_reason: str,
    assessment: str,
) -> dict[str, Any]:
    action = str(recommendation.get("action") or "").strip()
    action_category = _classify_action(action)
    code = _normalize_fund_code(recommendation.get("fund_code"))
    raw_code = str(recommendation.get("fund_code") or "").strip()
    return {
        "fund_code": code or raw_code,
        "fund_name": str(recommendation.get("fund_name") or ""),
        "action": action,
        "action_category": action_category,
        "eligible": action_category == "buy",
        "mature": False,
        "skipped": True,
        "skip_reason": skip_reason,
        "status": "skipped",
        "horizon_trading_days": days,
        "baseline_policy": "first_valuation_on_or_after_executable_date",
        "maturity_basis": "fund_nav_valuation_dates",
        "executable_date": None,
        "baseline_nav_date": None,
        "baseline_nav": None,
        "target_nav_date": None,
        "target_nav": None,
        "latest_nav": None,
        "latest_nav_date": None,
        "observed_forward_trading_days": 0,
        "period_change_percent": None,
        "partial_change_percent": None,
        "direction_aligned": None,
        "assessment": assessment,
        "hit_take_profit_within_days": None,
        **_item_benchmark_unavailable(),
    }


def _data_skipped_outcome(
    *,
    code: str,
    fund_name: str,
    action: str,
    days: int,
    executable_date: str,
    reason: str,
    assessment: str,
) -> dict[str, Any]:
    item = _skipped_item(
        {"fund_code": code, "fund_name": fund_name, "action": action},
        days=days,
        skip_reason=reason,
        assessment=assessment,
    )
    item["executable_date"] = executable_date
    return item


def _classify_action(action: str) -> str:
    normalized = str(action or "").strip()
    if normalized in _BUY_ACTIONS:
        return "buy"
    if normalized in _WATCH_ACTIONS:
        return "watch_only"
    if normalized in _CONDITIONAL_ACTIONS:
        return "conditional_wait"
    return "unknown"


def _ineligible_assessment(
    action_category: str,
    action: str,
    *,
    has_entry_trigger: bool = False,
) -> str:
    if action_category == "watch_only":
        return f"动作“{action or '建议关注'}”仅代表观察，不视作买入方向且不计命中。"
    if action_category == "conditional_wait":
        if has_entry_trigger:
            return "“等待回调”已记录可验证的入场触发点，条件满足前不计命中。"
        return "“等待回调”缺少可验证的入场触发点，单列为条件动作且不计命中。"
    return f"动作“{action or '空'}”不在可评价动作契约中，已跳过且不计命中。"


def _normalize_fund_code(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw.isdigit() or len(raw) > 6:
        return None
    code = raw.zfill(6)
    return code if code != "000000" else None


def _normalize_nav_points(rows: list) -> list[tuple[str, float]]:
    by_date: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_text = str(row.get("date") or "").strip()[:10]
        try:
            normalized_date = date.fromisoformat(date_text).isoformat()
        except ValueError:
            continue
        nav = _as_float(row.get("nav"))
        if nav is None or nav <= 0:
            continue
        # 同一估值日若供应商返回修订行，以最后一行作为当前官方值。
        by_date[normalized_date] = nav
    return sorted(by_date.items())


def _resolve_executable_date(since: datetime) -> str:
    moment = since
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(_CN_TZ)
    executable = local.date()
    if (local.hour, local.minute, local.second) >= (
        _MARKET_CLOSE.hour,
        _MARKET_CLOSE.minute,
        0,
    ):
        executable += timedelta(days=1)
    return executable.isoformat()


def _nav_pull_days(executable_date: str, horizon_days: int) -> int:
    try:
        age_calendar_days = max((datetime.now(_CN_TZ).date() - date.fromisoformat(executable_date)).days, 0)
    except ValueError:
        age_calendar_days = 0
    estimated_elapsed_trading_days = age_calendar_days * 5 // 7
    return min(
        800,
        max(90, horizon_days + 20, estimated_elapsed_trading_days + horizon_days + 20),
    )


def _benchmark_unavailable() -> dict[str, Any]:
    return {
        "available": False,
        "reason": _BENCHMARK_UNAVAILABLE_REASON,
        "benchmark_code": None,
        "period_change_percent": None,
    }


def _item_benchmark_unavailable() -> dict[str, Any]:
    return {
        "benchmark_available": False,
        "benchmark_reason": _BENCHMARK_UNAVAILABLE_REASON,
        "benchmark_code": None,
        "benchmark_change_percent": None,
        "excess_return_percent": None,
    }


def _normalize_horizon_days(days: object) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 7
    return max(1, min(value, _MAX_HORIZON_DAYS))


def _resolve_take_profit_threshold(report: dict[str, Any]) -> float | None:
    facts = report.get("discovery_facts") or {}
    profile = facts.get("profile") or {}
    profile_threshold = _as_float(profile.get("take_profit_threshold_percent"))
    if profile_threshold is not None and profile_threshold >= 0:
        return profile_threshold
    return None


def _direction_aligned(action: str, change_percent: float) -> bool:
    return action in _BUY_ACTIONS and change_percent > 0


def _assessment_label(
    action: str,
    change_percent: float,
    aligned: bool,
    *,
    days: int,
    metrics: dict[str, Any] | None = None,
) -> str:
    direction = "上涨" if change_percent > 0 else "下跌" if change_percent < 0 else "持平"
    verdict = "买入方向取得正收益" if aligned else "买入方向未取得正收益"
    details: list[str] = []
    metric_rows = metrics or {}
    net = (metric_rows.get("positive_net_return") or {}).get("value_percent")
    if net is not None:
        details.append(f"按冻结的用户费用假设后 {float(net):+.2f}%")
    excess = (metric_rows.get("gross_excess") or {}).get("value_percent")
    if excess is not None:
        details.append(f"相对基金合同基准 {float(excess):+.2f}%")
    suffix = f"；{'；'.join(details)}" if details else "；费后或正式基准暂不可评价"
    return (
        f"T+{days} {direction} {change_percent:+.2f}%，{verdict}"
        f"（动作：{action}{suffix}）"
    )


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None
