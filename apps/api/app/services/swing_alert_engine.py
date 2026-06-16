from __future__ import annotations

from app.models import Holding, InvestorProfile, SwingAlertItem, SwingMonitorScope
from app.services.aggressive_swing_recommendations import (
    _should_dip_buy,
    _should_take_profit_on_reversal,
)
from app.services.holding_estimates import compute_estimated_holding_return_percent
from app.services.investment_presets import take_profit_threshold_percent
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_intraday_summary import summarize_sector_intraday_for_holding
from app.services.sector_momentum import build_sector_momentum_context
from app.services.trading_session import build_trading_session

_INTRADAY_SESSION_KINDS = frozenset({"trading_day_intraday", "trading_day_pre_close"})
_SECTOR_DIP_1D = -2.5
_SECTOR_DIP_5D = -5.0


def should_evaluate_swing_alerts(profile: InvestorProfile) -> bool:
    if not profile.swing_alerts_enabled and profile.decision_style != "aggressive":
        return False
    session = build_trading_session()
    return session.get("session_kind") in _INTRADAY_SESSION_KINDS


def evaluate_swing_alerts(
    holdings: list[Holding],
    profile: InvestorProfile,
    *,
    monitor_scope: SwingMonitorScope | None = None,
    sector_heat: list[dict] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
) -> tuple[list[SwingAlertItem], str, str]:
    session = build_trading_session()
    trade_date = str(session.get("effective_trade_date") or "")
    session_kind = str(session.get("session_kind") or "")

    if not should_evaluate_swing_alerts(profile):
        return [], trade_date, session_kind

    scope = monitor_scope or profile.swing_monitor_scope or "both"
    nav_trends = nav_trends_by_code or {}
    items: list[SwingAlertItem] = []

    if scope in {"holdings", "both"}:
        items.extend(_evaluate_holding_alerts(holdings, profile, nav_trends))

    if scope in {"full_market", "both"} and sector_heat:
        held_sectors = {
            (holding.sector_name or "").strip()
            for holding in holdings
            if (holding.sector_name or "").strip()
        }
        items.extend(_evaluate_sector_alerts(sector_heat, held_sectors))

    items.sort(key=lambda row: (0 if row.priority == "high" else 1, row.alert_key))
    return items, trade_date, session_kind


def _evaluate_holding_alerts(
    holdings: list[Holding],
    profile: InvestorProfile,
    nav_trends: dict[str, dict],
) -> list[SwingAlertItem]:
    if not holdings:
        return []

    threshold = take_profit_threshold_percent(profile)
    denominator = resolve_weight_denominator(holdings, profile) or 1.0
    alerts: list[SwingAlertItem] = []

    for holding in holdings:
        if holding.holding_amount <= 0:
            continue
        nav_trend = nav_trends.get(holding.fund_code)
        momentum = build_sector_momentum_context(holding, nav_trend)
        intraday = summarize_sector_intraday_for_holding(holding)
        sector = holding.sector_return_percent
        est_return = compute_estimated_holding_return_percent(holding)
        weight = holding_weight_percent(holding, holdings, profile)
        code = holding.fund_code

        if est_return is not None and est_return >= threshold:
            alerts.append(
                SwingAlertItem(
                    alert_key=f"holding:{code}:take_profit",
                    alert_type="take_profit",
                    priority="high",
                    title=f"{holding.fund_name} 达止盈线",
                    message=(
                        f"持有收益约 {est_return:+.2f}% ≥ 扣费止盈线 {threshold:.1f}%，"
                        "激进波段建议考虑减仓落袋。"
                    ),
                    fund_code=code,
                    fund_name=holding.fund_name,
                )
            )
            continue

        if _should_take_profit_on_reversal(momentum, intraday, est_return, threshold):
            label = (momentum or {}).get("pattern_label") or (intraday or {}).get("pattern_label")
            alerts.append(
                SwingAlertItem(
                    alert_key=f"holding:{code}:pullback",
                    alert_type="pullback",
                    priority="high",
                    title=f"{holding.fund_name} 冲高回落",
                    message=f"检测到短线回吐信号（{label}），若有浮盈可优先止盈。",
                    fund_code=code,
                    fund_name=holding.fund_name,
                )
            )
            continue

        if weight > profile.concentration_limit_percent:
            continue

        if _should_dip_buy(sector, momentum, intraday, nav_trend):
            sector_text = f"{sector:+.2f}%" if sector is not None else "—"
            alerts.append(
                SwingAlertItem(
                    alert_key=f"holding:{code}:dip_buy",
                    alert_type="dip_buy",
                    priority="medium",
                    title=f"{holding.fund_name} 回调买入观察",
                    message=(
                        f"板块当日 {sector_text}，出现跌深企稳信号，可小额分批试探（非抄底承诺）。"
                    ),
                    fund_code=code,
                    fund_name=holding.fund_name,
                    sector_label=holding.sector_name,
                )
            )

    return alerts


def _evaluate_sector_alerts(
    sector_heat: list[dict],
    held_sectors: set[str],
) -> list[SwingAlertItem]:
    alerts: list[SwingAlertItem] = []
    for row in sector_heat:
        label = str(row.get("sector_label") or "").strip()
        if not label:
            continue
        change_1d = _num(row.get("change_1d_percent"))
        change_5d = _num(row.get("change_5d_percent"))
        dip = (
            change_1d is not None
            and change_1d <= _SECTOR_DIP_1D
            or change_5d is not None
            and change_5d <= _SECTOR_DIP_5D
        )
        if not dip:
            continue
        held_hint = "（已持仓板块）" if label in held_sectors else ""
        parts: list[str] = []
        if change_1d is not None:
            parts.append(f"当日 {change_1d:+.2f}%")
        if change_5d is not None:
            parts.append(f"近5日 {change_5d:+.2f}%")
        alerts.append(
            SwingAlertItem(
                alert_key=f"sector:{label}:sector_dip",
                alert_type="sector_dip",
                priority="medium",
                title=f"{label} 跌深观察{held_hint}",
                message=f"{'，'.join(parts)}，可关注跌深反弹机会（全市场扫描）。",
                sector_label=label,
            )
        )
    return alerts


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
