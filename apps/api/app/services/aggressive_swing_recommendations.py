from __future__ import annotations

from app.models import FundRecommendation, Holding, InvestorProfile, NewsItem
from app.services.holding_estimates import compute_estimated_holding_return_percent
from app.services.holding_metrics import compute_estimated_daily_return_percent
from app.services.investment_presets import take_profit_threshold_percent
from app.services.recommendations import attach_sector_news, suggest_trade_amount
from app.services.sector_intraday_summary import summarize_sector_intraday_for_holding
from app.services.sector_momentum import build_sector_momentum_context


def build_aggressive_swing_offline_fund_recommendation(
    holding: Holding,
    weight_percent: float,
    weight_denominator: float,
    profile: InvestorProfile,
    market_news: list[NewsItem] | None = None,
    *,
    nav_trend: dict | None = None,
    northbound_net_yi: float | None = None,
) -> FundRecommendation:
    _ = northbound_net_yi
    momentum = build_sector_momentum_context(holding, nav_trend)
    intraday = summarize_sector_intraday_for_holding(holding)
    sector = holding.sector_return_percent
    threshold = take_profit_threshold_percent(profile)
    est_return = compute_estimated_holding_return_percent(holding)
    action = "观察"
    points: list[str] = []

    if weight_percent > profile.concentration_limit_percent:
        action = "减仓评估"
        points.append(
            f"仓位 {weight_percent:.1f}% 超上限 {profile.concentration_limit_percent:.0f}%，激进波段仍须先降集中度。"
        )
    elif est_return is not None and est_return >= threshold:
        action = "减仓评估"
        points.append(
            f"持有收益约 {est_return:+.2f}% 已达扣费止盈线 {threshold:.1f}%"
            f"（手续费 {profile.round_trip_fee_percent or 1.5}% + 净赚目标 {profile.min_net_profit_percent or 1.0}%），建议落袋。"
        )
    elif _should_take_profit_on_reversal(momentum, intraday, est_return, threshold):
        action = "减仓评估"
        label = (momentum or {}).get("pattern_label") or (intraday or {}).get("pattern_label")
        points.append(f"短线冲高回落（{label}），已达部分浮盈，激进模式优先止盈。")
    elif _should_dip_buy(sector, momentum, intraday, nav_trend):
        action = "分批加仓"
        points.append(_dip_buy_reason(sector, momentum, intraday, nav_trend))
        points.append(
            f"持有目标 {profile.hold_days_target or 7} 天内；反弹未达 {threshold:.1f}% 应转观察或小减仓。"
        )
    elif sector is not None and sector > 5:
        action = "观察"
        points.append(f"板块当日 {sector:+.2f}% 偏热，激进模式亦不追涨，等待回调。")
    else:
        points.append("等待跌深企稳信号或持有收益达扣费止盈线。")

    points.extend(_holding_snapshot_lines(holding))
    if holding.fund_code == "000000":
        points.append("基金代码未补全，补全后可核对净值与公告。")

    amount_yuan, amount_note = suggest_trade_amount(
        holding, weight_percent, weight_denominator, profile, action
    )
    rec = FundRecommendation(
        fund_code=holding.fund_code,
        fund_name=holding.fund_name,
        action=action,
        amount_yuan=amount_yuan,
        amount_note=amount_note,
        points=points,
    )
    return attach_sector_news(rec, holding, market_news or [])


def _should_take_profit_on_reversal(
    momentum: dict | None,
    intraday: dict | None,
    est_return: float | None,
    threshold: float,
) -> bool:
    if est_return is None or est_return < threshold * 0.6:
        return False
    if momentum and momentum.get("pattern_label") == "two_day_reversal_down":
        return True
    if intraday and intraday.get("pattern_label") == "intraday_pullback":
        return True
    return False


def _should_dip_buy(
    sector: float | None,
    momentum: dict | None,
    intraday: dict | None,
    nav_trend: dict | None,
) -> bool:
    recent_5d = _num((nav_trend or {}).get("recent_5d_change_percent"))
    intraday_label = (intraday or {}).get("pattern_label")
    momentum_label = (momentum or {}).get("pattern_label")

    if recent_5d is not None and recent_5d <= -4.0:
        if sector is None or sector <= 1.5:
            return True

    if sector is not None and sector <= -1.5:
        if intraday_label in {"intraday_rebound", "steady_rally"} or sector > -4.0:
            return True
        if momentum_label != "two_day_reversal_down":
            return True

    if recent_5d is not None and recent_5d <= -2.5 and intraday_label == "intraday_rebound":
        return True

    return False


def _dip_buy_reason(
    sector: float | None,
    momentum: dict | None,
    intraday: dict | None,
    nav_trend: dict | None,
) -> str:
    parts: list[str] = []
    recent_5d = _num((nav_trend or {}).get("recent_5d_change_percent"))
    if recent_5d is not None and recent_5d < 0:
        parts.append(f"近5日 {recent_5d:+.2f}%")
    if sector is not None:
        parts.append(f"板块当日 {sector:+.2f}%")
    if intraday and intraday.get("pattern_label"):
        parts.append(f"分时 {intraday['pattern_label']}")
    if momentum and momentum.get("pattern_label"):
        parts.append(f"净值动能 {momentum['pattern_label']}")
    return "激进波段回调买入：" + "，".join(parts) + "，可小额分批试探（非抄底承诺）。"


def _holding_snapshot_lines(holding: Holding) -> list[str]:
    estimated_daily = compute_estimated_daily_return_percent(holding)
    daily = "-" if holding.daily_profit is None else f"{holding.daily_profit:.2f}"
    if holding.daily_return_percent is not None:
        daily_return = f"{holding.daily_return_percent:.2f}%"
    elif estimated_daily is not None:
        daily_return = f"≈{estimated_daily:.2f}%"
    else:
        daily_return = "-"
    est = compute_estimated_holding_return_percent(holding)
    holding_return = "-" if est is None else f"{est:.2f}%"
    sector = (
        f"{holding.sector_return_percent:.2f}%"
        if holding.sector_return_percent is not None
        else "-"
    )
    return [
        f"当日收益 {daily}（{daily_return}），持有收益 {holding_return}，板块 {sector}。"
    ]


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
