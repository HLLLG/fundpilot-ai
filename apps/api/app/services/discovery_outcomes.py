from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.akshare_subprocess import fetch_fund_nav_history


def build_discovery_outcomes(
    report: dict[str, Any],
    *,
    days: int = 7,
    fetch_nav=fetch_fund_nav_history,
) -> dict[str, Any]:
    recommendations = report.get("recommendations") or []
    if not recommendations:
        return {
            "has_data": False,
            "message": "该报告无推荐条目，无法复盘。",
            "items": [],
        }

    created_at = _parse_datetime(report.get("created_at"))
    if created_at is None:
        return {
            "has_data": False,
            "message": "报告时间解析失败，无法复盘。",
            "items": [],
        }

    items: list[dict[str, Any]] = []
    fee_threshold = _resolve_fee_break_even(report)
    take_profit_window = 3
    for rec in recommendations:
        code = str(rec.get("fund_code", "")).strip().zfill(6)
        if not code.isdigit():
            continue
        outcome = _outcome_for_fund(
            code=code,
            fund_name=str(rec.get("fund_name", "")),
            action=str(rec.get("action") or ""),
            since=created_at,
            days=days,
            fetch_nav=fetch_nav,
            fee_break_even_percent=fee_threshold,
            take_profit_days=take_profit_window,
        )
        if outcome is not None:
            items.append(outcome)

    if not items:
        return {
            "has_data": False,
            "message": "暂无法获取推荐基金的净值走势，请稍后再试。",
            "items": [],
        }

    aligned = sum(1 for item in items if item.get("direction_aligned"))
    take_profit_hits = sum(1 for item in items if item.get("hit_take_profit_within_days"))
    message = (
        f"自推荐日起约 {days} 个交易日内，{aligned}/{len(items)} 只方向与净值变化一致（历史统计，不代表未来）。"
    )
    if fee_threshold is not None and any(item.get("hit_take_profit_within_days") is not None for item in items):
        message += (
            f" 其中 {take_profit_hits}/{len(items)} 只在 {take_profit_window} 个交易日内达扣费止盈线"
            f"（≥{fee_threshold}% ，历史统计，不代表未来）。"
        )
    return {
        "has_data": True,
        "days": days,
        "message": message,
        "items": items,
    }


def build_discovery_recommendation_accuracy(
    reports: list[dict[str, Any]],
    *,
    days: int = 30,
    fetch_nav=fetch_fund_nav_history,
) -> dict[str, Any]:
    if not reports:
        return {
            "days": days,
            "sample_count": 0,
            "hit_rate_percent": None,
            "message": "暂无推荐报告样本。",
        }

    total = 0
    hits = 0
    for report in reports:
        outcome = build_discovery_outcomes(report, days=days, fetch_nav=fetch_nav)
        if not outcome.get("has_data"):
            continue
        for item in outcome.get("items", []):
            if item.get("period_change_percent") is None:
                continue
            total += 1
            if item.get("direction_aligned"):
                hits += 1

    rate = round(hits / total * 100, 1) if total else None
    return {
        "days": days,
        "sample_count": total,
        "hit_rate_percent": rate,
        "message": (
            f"近 {len(reports)} 份报告中，共 {total} 条可复盘推荐；"
            f"方向命中率 {rate}%。" if rate is not None else "样本不足，暂无命中率。"
        ),
    }


def _outcome_for_fund(
    *,
    code: str,
    fund_name: str,
    action: str,
    since: datetime,
    days: int,
    fetch_nav,
    fee_break_even_percent: float | None = None,
    take_profit_days: int = 3,
) -> dict[str, Any] | None:
    payload = fetch_nav(code, trading_days=max(days + 10, 30))
    rows = None
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("rows")
    if not rows:
        return None

    since_date = since.date().isoformat()
    baseline_nav = None
    latest_nav = None
    latest_date = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date", ""))[:10]
        nav = _as_float(row.get("nav"))
        if nav is None or nav <= 0:
            continue
        if date >= since_date and baseline_nav is None:
            baseline_nav = nav
        latest_nav = nav
        latest_date = date

    if baseline_nav is None or latest_nav is None or baseline_nav <= 0:
        return None

    change = round((latest_nav / baseline_nav - 1) * 100, 2)
    aligned = _direction_aligned(action, change)
    hit_take_profit = _hit_take_profit_within_days(
        rows,
        since_date=since_date,
        forward_trading_days=take_profit_days,
        threshold_percent=fee_break_even_percent,
    )
    return {
        "fund_code": code,
        "fund_name": fund_name,
        "action": action,
        "period_change_percent": change,
        "baseline_nav": round(baseline_nav, 4),
        "latest_nav": round(latest_nav, 4),
        "latest_nav_date": latest_date,
        "direction_aligned": aligned,
        "assessment": _assessment_label(action, change, aligned),
        "hit_take_profit_within_days": hit_take_profit,
    }


def _hit_take_profit_within_days(
    rows: list,
    *,
    since_date: str,
    forward_trading_days: int,
    threshold_percent: float | None,
) -> bool | None:
    if threshold_percent is None:
        return None

    baseline_nav = None
    baseline_date = None
    parsed_rows: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date", ""))[:10]
        nav = _as_float(row.get("nav"))
        if nav is None or nav <= 0 or not date:
            continue
        parsed_rows.append((date, nav))

    for date, nav in parsed_rows:
        if date >= since_date and baseline_nav is None:
            baseline_nav = nav
            baseline_date = date
            break

    if baseline_nav is None or baseline_date is None:
        return None

    forward_navs: list[float] = []
    for date, nav in parsed_rows:
        if date <= baseline_date:
            continue
        forward_navs.append(nav)
        if len(forward_navs) >= forward_trading_days:
            break

    if not forward_navs:
        return False

    target_nav = baseline_nav * (1.0 + threshold_percent / 100.0)
    return any(nav >= target_nav for nav in forward_navs)


def _resolve_fee_break_even(report: dict[str, Any]) -> float | None:
    facts = report.get("discovery_facts") or {}
    dip = facts.get("dip_swing") or {}
    if dip.get("fee_break_even_percent") is not None:
        try:
            return float(dip["fee_break_even_percent"])
        except (TypeError, ValueError):
            pass
    for rec in report.get("recommendations") or []:
        if rec.get("fee_break_even_percent") is not None:
            try:
                return float(rec["fee_break_even_percent"])
            except (TypeError, ValueError):
                continue
    profile = facts.get("profile") or {}
    if profile.get("take_profit_threshold_percent") is not None:
        try:
            return float(profile["take_profit_threshold_percent"])
        except (TypeError, ValueError):
            pass
    return None


def _direction_aligned(action: str, change_percent: float) -> bool:
    if action == "等待回调":
        return change_percent <= 0.5
    if action == "分批买入":
        return change_percent >= -1.0
    return True


def _assessment_label(action: str, change_percent: float, aligned: bool) -> str:
    direction = "上涨" if change_percent > 0 else "下跌" if change_percent < 0 else "持平"
    verdict = "与建议方向大致一致" if aligned else "与建议方向不完全一致"
    return f"区间{direction} {change_percent:+.2f}%，{verdict}（动作：{action}）"


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
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
