from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from heapq import nsmallest
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

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


DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 20)
METRIC_STATUS = "forward_nav_v1"
METRIC_VERSION = "daily_forward_nav_v1"
DECISION_EVENT_SCHEMA_VERSION = "decision_event.v1"
OUTCOME_OBSERVATION_SCHEMA_VERSION = "outcome_observation.v1"

_CN_TZ = ZoneInfo("Asia/Shanghai")
_AFTER_CUTOFF_SESSIONS = {"trading_day_after_close", "non_trading_day"}

NavFetcher = Callable[..., dict[str, Any] | None]


def normalize_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    """Validate and de-duplicate positive T+N trading-day horizons."""
    normalized: list[int] = []
    for value in horizons:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("horizon must be a positive integer trading-day count")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("at least one horizon is required")
    return tuple(sorted(normalized))


def report_calendar_date(report: dict[str, Any]) -> str | None:
    """Return the report's Shanghai calendar date, not its stale effective-data date."""
    facts = report.get("analysis_facts") or {}
    session = facts.get("session") or {}
    calendar_date = _iso_date(session.get("calendar_date"))
    if calendar_date:
        return calendar_date

    created = parse_report_datetime(report.get("created_at"))
    if created is None:
        return None
    return created.astimezone(_CN_TZ).date().isoformat()


def deduplicate_reports_by_calendar_date(
    reports: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep the latest report version for each Shanghai calendar day.

    ``created_at`` is the primary version key; report id is the deterministic
    tiebreaker. Reports without a parseable date are retained as independent
    invalid-date rows so the evaluator can expose them as skipped instead of
    silently discarding evidence.
    """
    rows = list(reports)
    selected: dict[str, dict[str, Any]] = {}
    for index, report in enumerate(rows):
        key = report_calendar_date(report) or f"__invalid_date__:{index}"
        current = selected.get(key)
        if current is None or _report_version_key(report) > _report_version_key(current):
            selected[key] = report

    kept = sorted(selected.values(), key=_report_version_key, reverse=True)
    metadata = {
        "key": "report_calendar_date",
        "strategy": "latest_created_at_then_id",
        "input_report_count": len(rows),
        "selected_report_count": len(kept),
        "duplicate_report_count": len(rows) - len(kept),
    }
    return kept, metadata


def recommended_nav_pull_days(
    report: dict[str, Any],
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> int:
    """Bound the NAV tail needed for an old report plus its longest horizon."""
    normalized = normalize_horizons(horizons)
    anchor = report_calendar_date(report)
    age_days = 0
    if anchor:
        try:
            age_days = max((date.today() - date.fromisoformat(anchor)).days, 0)
        except ValueError:
            age_days = 0
    # Calendar days deliberately over-estimate trading-day rows. Stable buckets
    # reuse the existing 252-day NAV cache/LRU instead of creating a new cache key
    # for every calendar day; the cap bounds malformed historical timestamps.
    required = min(max(90, age_days + max(normalized) + 20), 800)
    if required <= 252:
        return 252
    if required <= 400:
        return 400
    return 800


def evaluate_report_recommendations(
    report: dict[str, Any],
    *,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    fetch_nav: NavFetcher,
    trade_dates: frozenset[str] | None,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
    formal_v2_only: bool = False,
    legacy_reference_only: bool = False,
) -> dict[str, Any]:
    if formal_v2_only and legacy_reference_only:
        raise ValueError("formal_v2_only and legacy_reference_only are mutually exclusive")
    normalized_horizons = normalize_horizons(horizons)
    calendar_date = report_calendar_date(report)
    session_kind = _session_kind(report)
    calendar_source = "trade_calendar_cache" if trade_dates else "weekday_fallback"
    execution_date = _execution_trade_date(
        report,
        calendar_date=calendar_date,
        session_kind=session_kind,
        trade_dates=trade_dates,
    )
    pull_days = recommended_nav_pull_days(report, normalized_horizons)

    raw_recommendations = report.get("fund_recommendations") or []
    recommendations = [entry for entry in raw_recommendations if isinstance(entry, dict)]
    items: list[dict[str, Any]] = []

    for recommendation_index, rec in enumerate(recommendations):
        code = _normalize_fund_code(rec.get("fund_code"))
        frozen_event = find_frozen_decision_event(
            report,
            recommendation_index=recommendation_index,
            fund_code=code,
        )
        formal_metric_event = is_formal_v2_metric_event(report, frozen_event)
        if formal_v2_only and not formal_metric_event:
            continue
        if legacy_reference_only and formal_metric_event:
            continue
        item_execution_date = execution_date
        if frozen_event is not None:
            item_execution_date = (
                _iso_date(frozen_event.get("executable_calendar_date")) or execution_date
            )
        item = _evaluate_recommendation(
            rec,
            report=report,
            report_id=str(report.get("id") or ""),
            decision_at=_canonical_decision_at(report.get("created_at")),
            recommendation_index=recommendation_index,
            execution_date=item_execution_date,
            horizons=normalized_horizons,
            pull_days=pull_days,
            fetch_nav=fetch_nav,
            frozen_event=frozen_event,
            fetch_benchmark=fetch_benchmark,
        )
        items.append(item)

    stats = {
        f"T+{horizon}": _summarize_horizon(items, horizon)
        for horizon in normalized_horizons
    }
    eligible_count = sum(1 for item in items if item["evaluation_class"] in {"bullish", "bearish"})
    observation_count = sum(1 for item in items if item["evaluation_class"] == "observation")
    invalid_count = len(items) - eligible_count - observation_count
    primary = stats[f"T+{normalized_horizons[0]}"]
    has_baseline = any(item.get("baseline_nav") is not None for item in items)
    has_any_mature_sample = any(
        int(horizon_stats.get("mature_count") or 0) > 0
        for horizon_stats in stats.values()
    )

    return {
        "metric_status": METRIC_STATUS,
        "metric_version": METRIC_VERSION,
        "event_contract": {
            "decision_event_schema_version": (
                "decision_event.v2"
                if any(
                    str((item.get("decision_event") or {}).get("schema_version") or "")
                    == "decision_event.v2"
                    for item in items
                )
                else DECISION_EVENT_SCHEMA_VERSION
            ),
            "outcome_observation_schema_version": (
                "outcome_observation.v2"
                if any(
                    str((item.get("decision_event") or {}).get("schema_version") or "")
                    == "decision_event.v2"
                    for item in items
                )
                else OUTCOME_OBSERVATION_SCHEMA_VERSION
            ),
            "persistence": (
                "persisted"
                if any(
                    str((item.get("decision_event") or {}).get("schema_version") or "")
                    == "decision_event.v2"
                    for item in items
                )
                else "dynamic_not_persisted"
            ),
            "metric_contract_version": METRIC_CONTRACT_VERSION,
        },
        "evaluation_basis": "official_fund_nav_valuation_dates",
        "report_id": report.get("id"),
        "report_calendar_date": calendar_date,
        "execution_calendar_date": execution_date,
        # Compatibility field: with fund-specific valuation calendars this is the
        # earliest executable calendar date, while each item carries its actual
        # baseline_nav_date.
        "execution_nav_date": execution_date,
        "session_kind": session_kind,
        "baseline_policy": "first_fund_valuation_on_or_after_executable_date",
        "trading_day_basis": "fund_nav_valuation_dates",
        "calendar_source": calendar_source,
        "horizons": list(normalized_horizons),
        "has_baseline": has_baseline,
        "has_data": has_any_mature_sample,
        "recommendation_count": len(items),
        "eligible_count": eligible_count,
        "observation_count": observation_count,
        "invalid_count": invalid_count,
        "mature_count": primary["mature_count"],
        "skipped_count": primary["skipped_count"],
        "coverage_percent": primary["coverage_percent"],
        "by_horizon": stats,
        "items": items,
        "message": _report_message(
            eligible_count=eligible_count,
            observation_count=observation_count,
            primary_horizon=normalized_horizons[0],
            primary=primary,
        ),
    }


def _evaluate_recommendation(
    rec: dict[str, Any],
    *,
    report: dict[str, Any],
    report_id: str,
    decision_at: str | None,
    recommendation_index: int,
    execution_date: str | None,
    horizons: tuple[int, ...],
    pull_days: int,
    fetch_nav: NavFetcher,
    frozen_event: dict[str, Any] | None,
    fetch_benchmark: BenchmarkFetcher | None,
) -> dict[str, Any]:
    code = _normalize_fund_code(rec.get("fund_code"))
    action = str(rec.get("action") or "").strip()
    evaluation_class = str(
        (frozen_event or {}).get("evaluation_class") or classify_action(action)
    )
    report_token = report_id or decision_at or execution_date or "unpersisted"
    event_id = str(
        (frozen_event or {}).get("event_id")
        or f"daily:{report_token}:{recommendation_index}:{code or 'invalid'}"
    )
    decision_event = frozen_event or {
        "schema_version": DECISION_EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "report_id": report_id or None,
        "recommendation_index": recommendation_index,
        "decision_at": decision_at,
        "decision_trade_date": execution_date,
        "fund_code": code,
        "action": action,
        "evaluation_class": evaluation_class,
    }
    fee_policy = (
        dict(frozen_event.get("fee_policy") or frozen_event.get("fee_model") or {})
        if frozen_event is not None
        else fee_policy_from_report(report, decision_kind="daily")
    )
    benchmark_spec = dict((frozen_event or {}).get("benchmark") or {})
    item: dict[str, Any] = {
        "fund_code": code or str(rec.get("fund_code") or "").strip(),
        "fund_name": str(rec.get("fund_name") or "").strip(),
        "action": action,
        # Kept for older clients while the UI migrates from adjacent-report wording.
        "current_action": action,
        "evaluation_class": evaluation_class,
        "decision_event": decision_event,
        "fee_policy": fee_policy,
        "benchmark": {
            "tier": str(benchmark_spec.get("tier") or "unavailable"),
            "available": False,
            "formal_excess_eligible": False,
            "return_percent": None,
            "reference_return_percent": None,
            "reason": str(
                benchmark_spec.get("reason") or "point_in_time_benchmark_not_frozen"
            ),
        },
        "baseline_nav": None,
        "baseline_nav_date": None,
        "by_horizon": {},
    }

    if evaluation_class == "invalid" or code is None or execution_date is None:
        reason = "invalid_fund_code" if code is None else "invalid_report_date"
        item["skip_reason"] = reason
        for horizon in horizons:
            metrics = evaluate_decision_metrics(
                gross_return_percent=None,
                evaluation_class=evaluation_class,
                fee_policy=fee_policy,
                benchmark_result=item["benchmark"],
            )
            item["by_horizon"][f"T+{horizon}"] = {
                "status": "invalid",
                "horizon_trading_days": horizon,
                "direction_hit": None,
                "metrics": metrics,
                **metric_aliases(metrics),
                "skip_reason": reason,
            }
        _attach_observation_contracts(item, event_id=event_id, horizons=horizons)
        item["assessment"] = "基金代码或报告日期无效，未进入评价分母。"
        return item

    try:
        payload = fetch_nav(code, trading_days=pull_days)
    except Exception as exc:  # Provider failure is evidence state, not an endpoint failure.
        payload = None
        item["provider_error"] = type(exc).__name__

    rows = parse_nav_rows(payload)
    baseline_index = next(
        (index for index, (day, _nav) in enumerate(rows) if day >= execution_date),
        None,
    )
    if baseline_index is None:
        item["skip_reason"] = "baseline_nav_unavailable"
        for horizon in horizons:
            status = "observation" if evaluation_class == "observation" else "data_unavailable"
            metrics = evaluate_decision_metrics(
                gross_return_percent=None,
                evaluation_class=evaluation_class,
                fee_policy=fee_policy,
                benchmark_result=item["benchmark"],
            )
            item["by_horizon"][f"T+{horizon}"] = {
                "status": status,
                "maturity_status": "data_unavailable",
                "horizon_trading_days": horizon,
                "target_nav_date": None,
                "available_forward_trading_days": 0,
                "direction_hit": None,
                "metrics": metrics,
                **metric_aliases(metrics),
                "skip_reason": "baseline_nav_unavailable",
            }
        _attach_observation_contracts(item, event_id=event_id, horizons=horizons)
        item["assessment"] = "缺少报告时点之后的可成交净值，暂不评价。"
        return item

    baseline_date, baseline_nav = rows[baseline_index]
    item["baseline_nav"] = round(baseline_nav, 6)
    item["baseline_nav_date"] = baseline_date
    available_forward_days = max(len(rows) - baseline_index - 1, 0)
    item["available_forward_trading_days"] = available_forward_days

    for horizon in horizons:
        key = f"T+{horizon}"
        target_index = baseline_index + horizon
        if target_index >= len(rows):
            metrics = evaluate_decision_metrics(
                gross_return_percent=None,
                evaluation_class=evaluation_class,
                fee_policy=fee_policy,
                benchmark_result=item["benchmark"],
            )
            status = "observation" if evaluation_class == "observation" else "immature"
            item["by_horizon"][key] = {
                "status": status,
                "maturity_status": "immature",
                "horizon_trading_days": horizon,
                "target_nav_date": None,
                "available_forward_trading_days": available_forward_days,
                "direction_hit": None,
                "metrics": metrics,
                **metric_aliases(metrics),
            }
            continue

        target_date, target_nav = rows[target_index]
        change = round((target_nav / baseline_nav - 1.0) * 100.0, 4)
        benchmark_result = evaluate_frozen_benchmark(
            benchmark_spec,
            baseline_date=baseline_date,
            target_date=target_date,
            is_frozen=frozen_event is not None,
            fetch_component=fetch_benchmark,
        )
        metrics = evaluate_decision_metrics(
            gross_return_percent=change,
            evaluation_class=evaluation_class,
            fee_policy=fee_policy,
            benchmark_result=benchmark_result,
        )
        if evaluation_class == "observation":
            status = "observation"
            direction_hit = None
        else:
            status = "mature"
            direction_hit = metrics["gross_direction"]["hit"]
        item["by_horizon"][key] = {
            "status": status,
            "maturity_status": "mature",
            "horizon_trading_days": horizon,
            "target_nav": round(target_nav, 6),
            "target_nav_date": target_date,
            "return_percent": change,
            "direction_hit": direction_hit,
            **metric_aliases(metrics),
            "benchmark": benchmark_result,
            "metrics": metrics,
        }

    mature_benchmarks = [
        result.get("benchmark")
        for result in item["by_horizon"].values()
        if isinstance(result.get("benchmark"), dict)
    ]
    if mature_benchmarks:
        item["benchmark"] = dict(mature_benchmarks[0])

    _attach_observation_contracts(item, event_id=event_id, horizons=horizons)
    item["assessment"] = _item_assessment(item, horizons[0])
    return item


def parse_nav_rows(payload: object) -> list[tuple[str, float]]:
    rows: object = None
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("rows")
    if not isinstance(rows, list):
        return []

    by_date: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _iso_date(row.get("date"))
        nav = _positive_float(row.get("nav"))
        if day and nav is not None:
            by_date[day] = nav
    return sorted(by_date.items())


def classify_action(action: str) -> str:
    text = str(action or "").strip()
    if not text:
        return "invalid"
    if any(token in text for token in ("清仓", "减仓", "暂停追涨", "卖出", "赎回")):
        return "bearish"
    if any(token in text for token in ("加仓", "定投", "买入", "申购", "分批")):
        return "bullish"
    # Unknown actions are conservatively treated as observation: they are visible
    # in coverage diagnostics but can never become automatic hits.
    return "observation"


def _summarize_horizon(items: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    key = f"T+{horizon}"
    eligible = [item for item in items if item["evaluation_class"] in {"bullish", "bearish"}]
    mature = [
        item
        for item in eligible
        if (item.get("by_horizon") or {}).get(key, {}).get("status") == "mature"
    ]
    hits = sum(
        1
        for item in mature
        if (item.get("by_horizon") or {}).get(key, {}).get("direction_hit") is True
    )
    mature_count = len(mature)
    immature_count = sum(
        1
        for item in eligible
        if (item.get("by_horizon") or {}).get(key, {}).get("status") == "immature"
    )
    data_unavailable_count = sum(
        1
        for item in eligible
        if (item.get("by_horizon") or {}).get(key, {}).get("status")
        == "data_unavailable"
    )
    hit_rate = round(hits / mature_count * 100.0, 1) if mature_count else None
    coverage = round(mature_count / len(eligible) * 100.0, 1) if eligible else None
    metric_summary = summarize_metrics(
        (item.get("by_horizon") or {}).get(key, {}).get("metrics")
        for item in items
    )
    return {
        "horizon_trading_days": horizon,
        "eligible_count": len(eligible),
        "mature_count": mature_count,
        "skipped_count": len(eligible) - mature_count,
        "immature_count": immature_count,
        "data_unavailable_count": data_unavailable_count,
        "hit_count": hits,
        "miss_count": mature_count - hits,
        "hit_rate_percent": min(hit_rate, 100.0) if hit_rate is not None else None,
        "coverage_percent": min(coverage, 100.0) if coverage is not None else None,
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "metrics": metric_summary,
        "gross_direction": metric_summary["gross_direction"],
        "positive_net_return": metric_summary["positive_net_return"],
        "gross_excess": metric_summary["gross_excess"],
        "net_excess": metric_summary["net_excess"],
    }


def _execution_trade_date(
    report: dict[str, Any],
    *,
    calendar_date: str | None,
    session_kind: str | None,
    trade_dates: frozenset[str] | None,
) -> str | None:
    if calendar_date is None:
        return None
    created = parse_report_datetime(report.get("created_at"))
    after_cutoff = session_kind in _AFTER_CUTOFF_SESSIONS
    if session_kind is None and created is not None:
        after_cutoff = created.astimezone(_CN_TZ).hour >= 15

    if not after_cutoff and _is_trade_date(calendar_date, trade_dates=trade_dates):
        return calendar_date
    return _shift_trading_days(calendar_date, 1, trade_dates=trade_dates)


def _shift_trading_days(
    anchor_date: str,
    count: int,
    *,
    trade_dates: frozenset[str] | None,
) -> str | None:
    if trade_dates:
        future = nsmallest(count, (day for day in trade_dates if day > anchor_date))
        return future[count - 1] if len(future) >= count else None

    try:
        cursor = date.fromisoformat(anchor_date)
    except ValueError:
        return None
    remaining = count
    for _ in range(370):
        cursor += timedelta(days=1)
        if cursor.weekday() >= 5:
            continue
        remaining -= 1
        if remaining == 0:
            return cursor.isoformat()
    return None


def _is_trade_date(value: str, *, trade_dates: frozenset[str] | None) -> bool:
    if trade_dates:
        return value in trade_dates
    try:
        return date.fromisoformat(value).weekday() < 5
    except ValueError:
        return False


def _attach_observation_contracts(
    item: dict[str, Any],
    *,
    event_id: str,
    horizons: tuple[int, ...],
) -> None:
    event_schema = str((item.get("decision_event") or {}).get("schema_version") or "")
    observation_schema = (
        "outcome_observation.v2"
        if event_schema == "decision_event.v2"
        else OUTCOME_OBSERVATION_SCHEMA_VERSION
    )
    for horizon in horizons:
        key = f"T+{horizon}"
        result = (item.get("by_horizon") or {}).get(key)
        if not isinstance(result, dict):
            continue
        target_date = result.get("target_nav_date")
        maturity_status = result.get("maturity_status") or result.get("status")
        result["outcome_observation"] = {
            "schema_version": observation_schema,
            "observation_id": f"{event_id}:{key}",
            "event_id": event_id,
            "horizon_trading_days": horizon,
            "target_trade_date": target_date,
            "target_date": target_date,
            # NAV evidence has trade-date granularity; no publication timestamp is
            # invented. A future persistence layer may add recorded_at separately.
            "observation_at": (
                None
                if observation_schema == "outcome_observation.v2"
                else target_date if maturity_status == "mature" else None
            ),
            "source_available_at": None,
            "time_granularity": (
                "fund_valuation_date"
                if observation_schema == "outcome_observation.v2"
                else "trade_date"
            ),
            "status": maturity_status,
            "mature": maturity_status == "mature",
            "source": "official_fund_nav" if maturity_status == "mature" else "not_observed",
            "baseline": {
                "date": item.get("baseline_nav_date"),
                "nav": item.get("baseline_nav"),
            },
            "target": {
                "date": target_date,
                "nav": result.get("target_nav"),
            },
            "metrics": result.get("metrics") or {},
            "benchmark": result.get("benchmark") or item.get("benchmark"),
            "fee_policy": item.get("fee_policy"),
        }


def _session_kind(report: dict[str, Any]) -> str | None:
    session = ((report.get("analysis_facts") or {}).get("session") or {})
    value = str(session.get("session_kind") or "").strip()
    return value or None


def _direction_hit(evaluation_class: str, change_percent: float) -> bool:
    if evaluation_class == "bullish":
        return change_percent > 0
    if evaluation_class == "bearish":
        return change_percent < 0
    return False


def _item_assessment(item: dict[str, Any], primary_horizon: int) -> str:
    key = f"T+{primary_horizon}"
    result = (item.get("by_horizon") or {}).get(key) or {}
    if item.get("evaluation_class") == "observation":
        if result.get("return_percent") is not None:
            return (
                f"观察/复核类动作单列；{key} 净值变化 "
                f"{result['return_percent']:+.2f}%，不计入方向命中率。"
            )
        return f"观察/复核类动作单列；{key} 尚未成熟，不计入方向命中率。"
    if result.get("status") == "mature":
        verdict = "方向一致" if result.get("direction_hit") else "方向不一致"
        details: list[str] = []
        net = result.get("positive_net_return_percent")
        if net is not None:
            details.append(f"按冻结的用户费用假设后 {float(net):+.2f}%")
        excess = result.get("gross_excess_return_percent")
        if excess is not None:
            details.append(f"相对基金合同基准 {float(excess):+.2f}%")
        suffix = f"；{'；'.join(details)}" if details else "；费后或正式基准暂不可评价"
        return (
            f"{key} 净值变化 {result['return_percent']:+.2f}%，{verdict}{suffix}。"
        )
    if result.get("status") == "immature":
        return (
            f"{key} 尚未成熟：当前只有 "
            f"{result.get('available_forward_trading_days', 0)} 个后续净值日。"
        )
    return "净值基准不可用，暂不评价。"


def _report_message(
    *,
    eligible_count: int,
    observation_count: int,
    primary_horizon: int,
    primary: dict[str, Any],
) -> str:
    if eligible_count == 0:
        return f"没有可做方向评价的建议；观察/复核类 {observation_count} 条已单列。"
    rate = primary.get("hit_rate_percent")
    rate_text = f"，方向命中率 {rate}%" if rate is not None else ""
    metrics = primary.get("metrics") or {}
    net = metrics.get("positive_net_return") or {}
    excess = metrics.get("gross_excess") or {}
    return (
        f"T+{primary_horizon} 可评价 {primary['mature_count']}/{eligible_count} 条"
        f"（覆盖率 {primary['coverage_percent']}%）{rate_text}；"
        f"用户假设费后覆盖 {net.get('mature_count', 0)}/{net.get('eligible_count', 0)} 条，"
        f"正式基金基准超额覆盖 {excess.get('mature_count', 0)}/{excess.get('eligible_count', 0)} 条；"
        f"观察/复核类 {observation_count} 条不进入命中分母。"
    )


def parse_report_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Historical reports were serialized from UTC-naive datetimes.
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _canonical_decision_at(value: object) -> str | None:
    parsed = parse_report_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _report_version_key(report: dict[str, Any]) -> tuple[datetime, str]:
    created = parse_report_datetime(report.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
    return created.astimezone(timezone.utc), str(report.get("id") or "")


def _normalize_fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    code = text.zfill(6)
    if len(code) != 6 or code == "000000":
        return None
    return code


def _iso_date(value: object) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
