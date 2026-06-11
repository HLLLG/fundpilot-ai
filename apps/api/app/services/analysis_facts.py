from __future__ import annotations

from app.models import (
    FundSnapshot,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.services.holding_metrics import (
    compute_estimated_daily_return_percent,
    holding_daily_return_is_estimated,
)
from app.services.market_flow_client import build_market_flow_context
from app.services.news_freshness import build_news_pipeline_context
from app.services.sector_signal_context import (
    build_signal_backtest_context,
    sector_labels_from_holdings,
    signal_backtest_for_sector,
)
from app.services.signal_guard_policy import resolve_signal_guard_policy
from app.services.trading_session import get_effective_trade_date
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_intraday_summary import summarize_sector_intraday_for_holding
from app.services.sector_momentum import build_sector_momentum_context


def build_analysis_facts(
    holdings: list[Holding],
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    profile: InvestorProfile,
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
    market_news: list[NewsItem] | None = None,
    *,
    session: dict | None = None,
    pipeline: dict | None = None,
    portfolio_trend: dict | None = None,
) -> dict:
    nav_trends = nav_trends_by_code or {}
    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    weight_denominator = resolve_weight_denominator(holdings, profile)
    snapshot_by_code = {item.fund_code: item for item in snapshots}
    sector_labels = sector_labels_from_holdings(holdings)
    signal_backtest = build_signal_backtest_context(sector_labels)
    guard_policy = resolve_signal_guard_policy(holdings)

    per_fund: list[dict] = []
    for holding in holdings:
        weight = holding_weight_percent(holding, holdings, profile)
        estimated_daily = compute_estimated_daily_return_percent(holding)
        snapshot = snapshot_by_code.get(holding.fund_code)
        per_fund.append(
            {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "holding_amount": round(holding.holding_amount, 2),
                "weight_percent": round(weight, 2),
                "holding_return_percent": holding.holding_return_percent
                if holding.holding_return_percent is not None
                else holding.return_percent,
                "sector_return_percent": holding.sector_return_percent,
                "daily_return_percent": holding.daily_return_percent,
                "estimated_daily_return_percent": estimated_daily,
                "daily_return_is_estimated": holding_daily_return_is_estimated(holding),
                "daily_profit": holding.daily_profit,
                "holding_profit": holding.holding_profit,
                "sector_name": holding.sector_name,
                "over_concentration": weight > profile.concentration_limit_percent,
                "latest_nav": snapshot.latest_nav if snapshot else None,
                "nav_date": snapshot.nav_date if snapshot else None,
                "fund_type": snapshot.fund_type if snapshot else None,
                "return_1y_percent": snapshot.return_1y_percent if snapshot else None,
                "max_drawdown_1y_percent": snapshot.max_drawdown_1y_percent if snapshot else None,
                "management_fee": snapshot.management_fee if snapshot else None,
                "fund_scale_yi": snapshot.fund_scale_yi if snapshot else None,
                "nav_trend": nav_trends.get(holding.fund_code),
                "sector_momentum": build_sector_momentum_context(
                    holding,
                    nav_trends.get(holding.fund_code),
                ),
                "sector_intraday": summarize_sector_intraday_for_holding(holding),
                "signal_backtest": signal_backtest_for_sector(
                    holding.sector_name,
                    signal_backtest,
                ),
            }
        )

    facts: dict = {
        "readonly": True,
        "instruction": "以下数字由系统计算，分析时不得改写；仅可基于它们做解释与建议。",
        "portfolio": {
            "total_amount": round(total_amount, 2),
            "weight_denominator": round(weight_denominator, 2),
            "expected_investment_amount": profile.expected_investment_amount,
            "decision_style": profile.decision_style,
            "holding_count": len(holdings),
            "weighted_return_percent": risk.weighted_return_percent,
            "risk_level": risk.level,
            "suggested_action": risk.suggested_action,
            "max_drawdown_limit_percent": profile.max_drawdown_percent,
            "concentration_limit_percent": profile.concentration_limit_percent,
        },
        "alerts": [alert.model_dump() for alert in risk.alerts],
        "holdings": per_fund,
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        "news": build_news_pipeline_context(market_news, topic_briefs),
    }
    if session:
        facts["session"] = session
    if pipeline:
        facts["pipeline"] = pipeline
    if portfolio_trend:
        facts["portfolio_trend"] = portfolio_trend
    facts["market_flow"] = build_market_flow_context(
        trade_date=get_effective_trade_date(),
    )
    facts["signal_backtest"] = signal_backtest
    facts["guard_policy"] = {
        "enforce_reversal_block": guard_policy.get("enforce_reversal_block", True),
        "enforce_pullback_block": guard_policy.get("enforce_pullback_block", True),
        "tighten_tactical": guard_policy.get("tighten_tactical", False),
        "reason": guard_policy.get("reason"),
        "backtest_summary_lines": guard_policy.get("backtest_summary_lines") or [],
    }
    if profile.decision_style == "tactical":
        facts["prompt_tuning"] = guard_policy
    return facts
