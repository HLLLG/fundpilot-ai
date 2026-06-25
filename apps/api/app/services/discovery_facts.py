from __future__ import annotations

from app.models import Holding, InvestorProfile, NewsItem, TopicBrief
from app.services.discovery_sector_context import (
    build_candidate_factor_scores,
    build_target_sector_context,
)
from app.services.investment_presets import take_profit_threshold_percent
from app.services.market_flow_client import build_market_flow_context
from app.services.news_freshness import build_news_pipeline_context
from app.services.risk import resolve_weight_denominator
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
        "instruction": (
            "以下数字由系统计算；推荐基金代码必须来自 candidate_pool。"
            "引用 sector_fund_flow / sector_intraday / signal_backtest 时须用给定数字，禁止编造。"
            "news.freshness_label 须在 summary 或 caveats 体现对决策置信度的影响。"
            "market_flow 为北向/南向资金解读；factor_scores 按 factor_reliability 置信使用。"
        ),
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
            "target_sectors": target_sectors,
            "scan_mode": scan_mode,
        },
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


def _held_sector_summary(holdings: list[Holding]) -> list[dict]:
    totals: dict[str, float] = {}
    for holding in holdings:
        label = (holding.sector_name or "未分类").strip() or "未分类"
        totals[label] = totals.get(label, 0.0) + holding.holding_amount
    return [
        {"sector_name": label, "amount": round(amount, 2)}
        for label, amount in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
