from __future__ import annotations

from app.models import Holding, InvestorProfile, NewsItem, TopicBrief
from app.services.discovery_sector_context import (
    build_candidate_factor_scores,
    build_target_sector_context,
)
from app.services.discovery_prompt import DISCOVERY_FACTS_INSTRUCTION
from app.services.investment_presets import take_profit_threshold_percent
from app.services.market_flow_client import build_market_flow_context
from app.services.fund_nav_service import get_cached_official_nav_return
from app.services.holding_estimates import (
    compute_estimated_daily_return_percent,
    resolve_holding_return_percent,
)
from app.services.news_freshness import build_news_pipeline_context
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_signal_context import build_signal_backtest_context
from app.services.trading_session import build_trading_session


def build_discovery_facts(
    *,
    holdings: list[Holding],
    profile: InvestorProfile,
    target_sectors: list[str],
    sector_heat: list[dict],
    candidate_pool: list[dict],
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    budget_yuan: float | None = None,
    selection_strategy: str = "balanced",
    scan_mode: str = "full_market",
    dip_lookback_days: int = 5,
    dip_min_drop_percent: float = 3.0,
    focus_sectors: list[str] | None = None,
    fund_type_preference: str = "any",
) -> dict:
    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    denominator = resolve_weight_denominator(holdings, profile)
    available_budget = budget_yuan
    if available_budget is None:
        expected = profile.expected_investment_amount or 0.0
        available_budget = max(expected - total_amount, 0.0)

    signal_backtest = build_signal_backtest_context(target_sectors)
    session = build_trading_session()
    fee_break_even = take_profit_threshold_percent(profile)
    target_exit_days = profile.hold_days_target or 5

    facts: dict = {
        "readonly": True,
        "instruction": DISCOVERY_FACTS_INSTRUCTION,
        "session": session,
        "profile": {
            "decision_style": profile.decision_style,
            "prefer_dca": profile.prefer_dca,
            "avoid_chasing": profile.avoid_chasing,
            "max_drawdown_percent": profile.max_drawdown_percent,
            "concentration_limit_percent": profile.concentration_limit_percent,
            "expected_investment_amount": profile.expected_investment_amount,
            "horizon": profile.horizon,
            **(
                {
                    "round_trip_fee_percent": profile.round_trip_fee_percent,
                    "min_net_profit_percent": profile.min_net_profit_percent,
                    "take_profit_threshold_percent": take_profit_threshold_percent(profile),
                    "hold_days_target": profile.hold_days_target,
                }
                if profile.decision_style == "aggressive"
                else {}
            ),
        },
        "portfolio_gap": {
            "holding_count": len(holdings),
            "total_amount": round(total_amount, 2),
            "available_budget_yuan": round(available_budget, 2),
            "held_sectors": _held_sector_summary(holdings),
            "holdings_slim": _build_holdings_slim(
                holdings,
                profile,
                trade_date=session.get("effective_trade_date"),
            ),
            "target_sectors": target_sectors,
            "scan_mode": scan_mode,
        },
        "fund_type_preference": fund_type_preference,
        "sector_heat": sector_heat,
        "target_sector_context": build_target_sector_context(
            list(dict.fromkeys(list(target_sectors) + list(focus_sectors or []))),
            sector_heat,
            signal_backtest,
            trade_date=session.get("effective_trade_date"),
        ),
        "market_flow": build_market_flow_context(session.get("effective_trade_date")),
        "signal_backtest": signal_backtest,
        "news": build_news_pipeline_context(market_news, topic_briefs),
        "candidate_pool": candidate_pool,
        "candidate_factor_scores": build_candidate_factor_scores(candidate_pool),
        "selection_strategy": selection_strategy,
    }

    if scan_mode == "dip_swing":
        dip_values = [
            float(item["dip_drop_percent"])
            for item in candidate_pool
            if item.get("dip_drop_percent") is not None
        ]
        avg_drop = round(sum(dip_values) / len(dip_values), 2) if dip_values else None
        facts["dip_swing"] = {
            "lookback_days": dip_lookback_days,
            "min_drop_percent": dip_min_drop_percent,
            "fee_break_even_percent": fee_break_even,
            "target_exit_days": target_exit_days,
            "pool_prescreen_stats": {
                "candidates": len(candidate_pool),
                "avg_drop": avg_drop,
            },
        }

    return facts


def _build_holdings_slim(
    holdings: list[Holding],
    profile: InvestorProfile,
    *,
    trade_date: str | None,
) -> list[dict]:
    rows: list[dict] = []
    for holding in holdings:
        effective = holding
        if trade_date and holding.fund_code and holding.fund_code != "000000":
            nav_return = get_cached_official_nav_return(holding.fund_code, trade_date)
            if nav_return is not None and holding.daily_return_percent_source != "official_nav":
                effective = holding.model_copy(
                    update={
                        "daily_return_percent": nav_return,
                        "daily_return_percent_source": "official_nav",
                    }
                )
        rows.append(
            {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "sector_name": holding.sector_name,
                "holding_amount": round(holding.holding_amount, 2),
                "weight_percent": round(
                    holding_weight_percent(holding, holdings, profile),
                    2,
                ),
                "holding_return_percent": resolve_holding_return_percent(holding),
                "estimated_daily_return_percent": compute_estimated_daily_return_percent(
                    effective
                ),
            }
        )
    return rows


def _held_sector_summary(holdings: list[Holding]) -> list[dict]:
    totals: dict[str, float] = {}
    for holding in holdings:
        label = (holding.sector_name or "未分类").strip() or "未分类"
        totals[label] = totals.get(label, 0.0) + holding.holding_amount
    return [
        {"sector_name": label, "amount": round(amount, 2)}
        for label, amount in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
