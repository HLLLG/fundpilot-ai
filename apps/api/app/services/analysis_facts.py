from __future__ import annotations

from app.models import (
    FundSnapshot,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.services.investment_presets import is_short_term_style, take_profit_threshold_percent
from app.services.holding_estimates import build_holding_display_metrics
from app.services.holding_metrics import (
    compute_estimated_daily_return_percent,
    compute_sector_fund_gap_percent,
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
from app.services.sector_fund_flow_context import (
    build_sector_fund_flow_map,
    sector_fund_flow_for_holding,
)


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
    factor_scores: dict | None = None,
    for_llm: bool = False,
) -> dict:
    nav_trends = nav_trends_by_code or {}
    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    weight_denominator = resolve_weight_denominator(holdings, profile)
    snapshot_by_code = {item.fund_code: item for item in snapshots}
    sector_labels = sector_labels_from_holdings(holdings)
    signal_backtest = build_signal_backtest_context(sector_labels)
    guard_policy = resolve_signal_guard_policy(holdings)
    sector_flow_map = build_sector_fund_flow_map(holdings)

    per_fund: list[dict] = []
    drawdown_limit = abs(profile.max_drawdown_percent)
    for holding in holdings:
        weight = holding_weight_percent(holding, holdings, profile)
        estimated_daily = compute_estimated_daily_return_percent(holding)
        display = build_holding_display_metrics(holding)
        effective_return = float(display["estimated_holding_return_percent"] or 0)
        snapshot = snapshot_by_code.get(holding.fund_code)
        row: dict = {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "holding_amount": round(holding.holding_amount, 2),
                "weight_percent": round(weight, 2),
                "holding_return_percent": display["holding_return_percent_settled"],
                "estimated_holding_return_percent": round(effective_return, 4),
                "estimated_holding_profit": display["estimated_holding_profit"],
                "holding_return_is_estimated": display["holding_return_is_estimated"],
                "over_drawdown_limit": effective_return <= -drawdown_limit,
                "sector_return_percent": holding.sector_return_percent,
                "sector_return_percent_source": holding.sector_return_percent_source,
                "daily_return_percent": holding.daily_return_percent,
                "daily_return_percent_source": holding.daily_return_percent_source,
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
                "sector_fund_flow": sector_fund_flow_for_holding(holding, sector_flow_map),
                "signal_backtest": signal_backtest_for_sector(
                    holding.sector_name,
                    signal_backtest,
                ),
            }
        if for_llm:
            row["sector_fund_gap_percent"] = compute_sector_fund_gap_percent(holding)
        per_fund.append(row)

    facts: dict = {
        "readonly": True,
        "instruction": (
            "以下数字由系统计算，分析时不得改写；仅可基于它们做解释与建议。"
            "浮亏/持有收益判断须用 estimated_holding_return_percent 与 portfolio.weighted_return_percent，"
            "勿用 holding_return_percent（昨日结算）。"
            "板块信号(signal_backtest)须按各规则 confidence.level 表述："
            "「高」可作主理由；「中」需措辞保留；「低/不足」只能作提示，"
            "不得据此主导追涨或减仓建议。"
            "因子分(factor_scores)须按 factor_reliability 各因子置信使用："
            "「高」可作论据；「中」措辞保留；「低/不足」仅作描述、不得作买卖主理由；"
            "size 因子未回测仅供参考。"
        ),
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
    if factor_scores:
        facts["factor_scores"] = factor_scores
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
    if is_short_term_style(profile.decision_style):
        facts["prompt_tuning"] = guard_policy
    return facts
