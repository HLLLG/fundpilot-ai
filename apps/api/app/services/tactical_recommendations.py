from __future__ import annotations

from app.models import FundRecommendation, Holding, InvestorProfile, NewsItem
from app.services.holding_metrics import compute_estimated_daily_return_percent
from app.services.recommendations import attach_sector_news, suggest_trade_amount
from app.services.sector_intraday_summary import summarize_sector_intraday_for_holding
from app.services.sector_momentum import build_sector_momentum_context


def build_tactical_offline_fund_recommendation(
    holding: Holding,
    weight_percent: float,
    weight_denominator: float,
    profile: InvestorProfile,
    market_news: list[NewsItem] | None = None,
    *,
    nav_trend: dict | None = None,
) -> FundRecommendation:
    momentum = build_sector_momentum_context(holding, nav_trend)
    intraday = summarize_sector_intraday_for_holding(holding)
    sector = holding.sector_return_percent
    action = "观察"
    points: list[str] = []

    if weight_percent > profile.concentration_limit_percent:
        action = "减仓评估"
        points.append(
            f"仓位 {weight_percent:.1f}% 超上限 {profile.concentration_limit_percent:.0f}%，战术模式仍须先降集中度。"
        )
    elif _should_take_profit(momentum, intraday):
        action = "减仓评估"
        label = (momentum or {}).get("pattern_label") or (intraday or {}).get("pattern_label")
        points.append(
            f"短线回吐/冲高回落信号（{label}），战术上优先锁定浮盈或减至上限以内。"
        )
    elif _should_momentum_add(sector, momentum, intraday):
        action = "分批加仓"
        points.append(_momentum_reason(sector, momentum, intraday))
        points.append("战术追涨：次日若板块冲高回落，应缩小加仓或转观察。")
    elif sector is not None and sector <= -2.0:
        action = "观察"
        points.append(f"板块当日 {sector:+.2f}% 偏弱，战术上不宜抄底，等待企稳信号。")
    else:
        points.append("短线动能中性，维持观察，等待板块+分时+要闻共振。")

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


def _should_take_profit(momentum: dict | None, intraday: dict | None) -> bool:
    if momentum and momentum.get("pattern_label") == "two_day_reversal_down":
        return True
    if intraday and intraday.get("pattern_label") == "intraday_pullback":
        return True
    if momentum and momentum.get("reversal_risk") == "high":
        return True
    return False


def _should_momentum_add(
    sector: float | None,
    momentum: dict | None,
    intraday: dict | None,
) -> bool:
    if sector is None:
        return False
    if sector < 1.0 or sector > 6.5:
        return False
    intraday_label = (intraday or {}).get("pattern_label")
    momentum_label = (momentum or {}).get("pattern_label")
    if intraday_label in {"steady_rally", "intraday_rebound"}:
        return True
    if momentum_label == "sector_fund_same_day_strong" and sector >= 2.0:
        return True
    return sector >= 3.0 and intraday_label != "intraday_pullback"


def _momentum_reason(
    sector: float | None,
    momentum: dict | None,
    intraday: dict | None,
) -> str:
    parts: list[str] = []
    if sector is not None:
        parts.append(f"板块 +{sector:.2f}%")
    if intraday and intraday.get("pattern_label"):
        parts.append(f"分时 {intraday['pattern_label']}")
    if momentum and momentum.get("pattern_label"):
        parts.append(f"净值动能 {momentum['pattern_label']}")
    return "战术动量共振：" + "，".join(parts) + "，可小额分批跟随。"


def _holding_snapshot_lines(holding: Holding) -> list[str]:
    estimated_daily = compute_estimated_daily_return_percent(holding)
    daily = "-" if holding.daily_profit is None else f"{holding.daily_profit:.2f}"
    if holding.daily_return_percent is not None:
        daily_return = f"{holding.daily_return_percent:.2f}%"
    elif estimated_daily is not None:
        daily_return = f"≈{estimated_daily:.2f}%"
    else:
        daily_return = "-"
    holding_return = (
        "-"
        if holding.holding_return_percent is None
        else f"{holding.holding_return_percent:.2f}%"
    )
    sector_change = (
        "-"
        if holding.sector_return_percent is None
        else f"{holding.sector_return_percent:.2f}%"
    )
    return [
        (
            f"当日收益 {daily}/{daily_return}；持有 {holding_return}；"
            f"板块 {holding.sector_name or '未知'} {sector_change}。"
        )
    ]
