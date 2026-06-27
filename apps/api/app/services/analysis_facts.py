from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from app.models import (
    FundSnapshot,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.request_context import try_get_request_user_id
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
from app.services.signal_synthesis import build_evidence_overview, build_holding_evidence
from app.services.trading_session import get_effective_trade_date
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_intraday_summary import summarize_sector_intraday_for_holding
from app.services.pipeline_concurrency import run_with_request_user
from app.services.sector_momentum import build_sector_momentum_context
from app.services.sector_fund_flow_context import (
    build_sector_fund_flow_map,
    sector_fund_flow_for_holding,
)
from app.services.sector_labels import normalize_sector_label
from app.services.sector_quote_label import sector_quote_lookup_label

SIGNAL_BACKTEST_TIMEOUT_SECONDS = 5.0
SECTOR_FLOW_TIMEOUT_SECONDS = 5.0
SECTOR_INTRADAY_TIMEOUT_SECONDS = 4.0
MARKET_FLOW_TIMEOUT_SECONDS = 3.0
GUARD_POLICY_TIMEOUT_SECONDS = 2.0


def _build_sector_intraday_map(holdings: list[Holding]) -> dict[str, dict]:
    """按板块 label 去重，复用全局 intraday 缓存。"""
    result: dict[str, dict] = {}
    for holding in holdings:
        label = sector_quote_lookup_label(holding)
        if not label or label in result:
            continue
        summary = summarize_sector_intraday_for_holding(holding)
        if summary is not None:
            result[label] = summary
    return result


def _daily_return_data_source(holding: Holding) -> str | None:
    if holding.daily_return_percent_source:
        return holding.daily_return_percent_source
    if holding.daily_return_percent is not None:
        return "daily_return"
    if holding.sector_return_percent is not None:
        return "sector_estimate"
    return None


def _build_data_freshness(per_fund: list[dict], effective_trade_date: str) -> dict:
    nav_dates = sorted(
        {str(row["nav_date"]) for row in per_fund if row.get("nav_date")}
    )
    daily_dates = sorted(
        {
            str(row["daily_return_trade_date"])
            for row in per_fund
            if row.get("daily_return_trade_date")
        }
    )
    return {
        "effective_trade_date": effective_trade_date,
        "daily_return_trade_dates": daily_dates,
        "official_nav_dates": nav_dates,
        "has_stale_nav_dates": any(
            nav_date != effective_trade_date for nav_date in nav_dates
        ),
        "note": (
            "effective_trade_date is today's trading/estimate date; nav_date is "
            "the latest official fund NAV date and may lag before NAV is published."
        ),
    }


def _run_budgeted_enhancement(
    func,
    *,
    timeout_seconds: float,
    fallback: Any,
) -> Any:
    user_id = try_get_request_user_id()

    def run():
        if user_id is None:
            return func()
        return run_with_request_user(user_id, func)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis-facts-budget")
    future = executor.submit(run)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        future.cancel()
        return fallback
    except Exception:  # noqa: BLE001 - enhancement facts are best-effort
        return fallback
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _submit_enhancement(executor: ThreadPoolExecutor, func):
    user_id = try_get_request_user_id()

    def run():
        if user_id is None:
            return func()
        return run_with_request_user(user_id, func)

    return executor.submit(run)


def _enhancement_result(future, *, timeout_seconds: float, fallback: Any) -> Any:
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        future.cancel()
        return fallback
    except Exception:  # noqa: BLE001 - enhancement facts are best-effort
        return fallback


def _signal_backtest_unavailable(reason: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "has_data": False,
        "reason": reason,
        "message": "板块信号回测未在预算内完成，日报已按基础事实继续。",
        "summary_lines": [],
        "sectors": [],
    }


def _market_flow_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "message": "市场资金流未在预算内完成，日报已按基础事实继续。",
    }


def _guard_policy_unavailable() -> dict[str, Any]:
    return {
        "enforce_reversal_block": True,
        "enforce_pullback_block": True,
        "tighten_tactical": False,
        "reason": "guard_policy_timeout",
        "backtest_summary_lines": [],
    }


def _sector_flow_timeout_map(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label or label in result:
            continue
        result[label] = {
            "available": False,
            "sector_label": label,
            "reason": "timeout",
            "message": "板块资金流未在预算内完成，日报已按基础事实继续。",
        }
    return result


def _build_budget_holding_display_metrics(holding: Holding) -> dict[str, float | bool | None]:
    settled = (
        holding.holding_return_percent
        if holding.holding_return_percent is not None
        else holding.return_percent
    )
    intraday = holding.daily_return_percent
    if intraday is None:
        intraday = holding.sector_return_percent

    if holding.daily_return_percent_source == "official_nav":
        estimated_return = settled if settled is not None else intraday
    elif settled is not None and intraday is not None:
        estimated_return = round(float(settled) + float(intraday), 4)
    elif settled is not None:
        estimated_return = float(settled)
    else:
        estimated_return = 0.0

    amount = holding.settled_holding_amount or holding.holding_amount
    estimated_profit = holding.holding_profit
    if holding.daily_return_percent_source != "official_nav":
        if estimated_profit is not None and intraday is not None and amount > 0:
            estimated_profit = round(float(estimated_profit) + amount * float(intraday) / 100, 2)
        elif estimated_profit is None and amount > 0 and estimated_return is not None:
            estimated_profit = round(amount * float(estimated_return) / (100 + float(estimated_return)), 2)

    return {
        "holding_return_percent_settled": settled,
        "estimated_holding_return_percent": estimated_return,
        "estimated_holding_profit": estimated_profit,
        "holding_return_is_estimated": holding.daily_return_percent_source != "official_nav"
        and intraday is not None,
    }


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
    risk_metrics: dict | None = None,
    for_llm: bool = False,
    budget_enhancements: bool = False,
) -> dict:
    nav_trends = nav_trends_by_code or {}
    effective_trade_date = (
        str(session.get("effective_trade_date"))
        if isinstance(session, dict) and session.get("effective_trade_date")
        else get_effective_trade_date()
    )
    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    weight_denominator = resolve_weight_denominator(holdings, profile)
    snapshot_by_code = {item.fund_code: item for item in snapshots}
    sector_labels = sector_labels_from_holdings(holdings)
    market_flow = None
    if budget_enhancements:
        executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="analysis-facts-budget")
        try:
            signal_future = _submit_enhancement(
                executor,
                lambda: build_signal_backtest_context(sector_labels),
            )
            guard_future = _submit_enhancement(
                executor,
                lambda: resolve_signal_guard_policy(holdings),
            )
            flow_future = _submit_enhancement(
                executor,
                lambda: build_sector_fund_flow_map(
                    holdings,
                    trade_date=effective_trade_date,
                ),
            )
            intraday_future = _submit_enhancement(
                executor,
                lambda: _build_sector_intraday_map(holdings),
            )
            market_flow_future = _submit_enhancement(
                executor,
                lambda: build_market_flow_context(trade_date=effective_trade_date),
            )
            signal_backtest = _enhancement_result(
                signal_future,
                timeout_seconds=SIGNAL_BACKTEST_TIMEOUT_SECONDS,
                fallback=_signal_backtest_unavailable("timeout"),
            )
            guard_policy = _enhancement_result(
                guard_future,
                timeout_seconds=GUARD_POLICY_TIMEOUT_SECONDS,
                fallback=_guard_policy_unavailable(),
            )
            sector_flow_map = _enhancement_result(
                flow_future,
                timeout_seconds=SECTOR_FLOW_TIMEOUT_SECONDS,
                fallback=_sector_flow_timeout_map(holdings),
            )
            intraday_map = _enhancement_result(
                intraday_future,
                timeout_seconds=SECTOR_INTRADAY_TIMEOUT_SECONDS,
                fallback={},
            )
            market_flow = _enhancement_result(
                market_flow_future,
                timeout_seconds=MARKET_FLOW_TIMEOUT_SECONDS,
                fallback=_market_flow_unavailable("timeout"),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    else:
        signal_backtest = build_signal_backtest_context(sector_labels)
        guard_policy = resolve_signal_guard_policy(holdings)
        sector_flow_map = build_sector_fund_flow_map(
            holdings,
            trade_date=effective_trade_date,
        )
        intraday_map = _build_sector_intraday_map(holdings)

    per_fund: list[dict] = []
    drawdown_limit = abs(profile.max_drawdown_percent)
    for holding in holdings:
        weight = holding_weight_percent(holding, holdings, profile)
        estimated_daily = compute_estimated_daily_return_percent(holding)
        display = (
            _build_budget_holding_display_metrics(holding)
            if budget_enhancements
            else build_holding_display_metrics(holding)
        )
        effective_return = float(display["estimated_holding_return_percent"] or 0)
        snapshot = snapshot_by_code.get(holding.fund_code)
        daily_return_source = _daily_return_data_source(holding)
        daily_return_trade_date = effective_trade_date if daily_return_source else None
        nav_date = snapshot.nav_date if snapshot else None
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
                "nav_date": nav_date,
                "daily_return_trade_date": daily_return_trade_date,
                "daily_return_data_source": daily_return_source,
                "nav_date_is_current_trade_date": (
                    nav_date == effective_trade_date if nav_date else None
                ),
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
                "sector_intraday": intraday_map.get(sector_quote_lookup_label(holding) or ""),
                "sector_fund_flow": (
                    sector_flow_map.get(normalize_sector_label(holding.sector_name))
                    if budget_enhancements
                    else sector_fund_flow_for_holding(holding, sector_flow_map)
                ),
                "signal_backtest": signal_backtest_for_sector(
                    holding.sector_name,
                    signal_backtest,
                ),
            }
        if for_llm:
            row["sector_fund_gap_percent"] = compute_sector_fund_gap_percent(holding)
        evidence = build_holding_evidence(
            fund_code=holding.fund_code,
            signal_entry=row["signal_backtest"],
            factor_scores=factor_scores,
            risk_metrics=risk_metrics,
        )
        if evidence:
            row["evidence"] = evidence
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
            "组合风险指标(risk_metrics：夏普/回撤/Beta/HHI)为系统计算事实，"
            "按 confidence.level 表述：「高/中」可作风险论据；"
            "「低/不足」须声明样本有限、不得据此下强结论。"
            "持仓的 evidence.composite 是该票三路量化证据(因子IC/板块信号/风险样本)的"
            "综合置信：「高」表多路背书一致、可作主理由；「中」部分支持；"
            "「低/不足」量化背书弱、须以风险口径表述、不得据此追涨。"
            "evidence_overview 是组合级量化背书体检：backed_weight_percent 为"
            "「中/高背书」市值占比；占比高→建议可更积极，占比低→须强调多数仓位"
            "量化背书不足、以风险口径表述。"
            "sector_fund_flow.today_main_force_net_yi 正数=净流入、负数=净流出；"
            "仅当 flow_date 与 trade_date 对齐（date_aligned=true）时方可与 sector_return_percent 做背离判断。"
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
        "data_freshness": _build_data_freshness(per_fund, effective_trade_date),
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
    if risk_metrics:
        facts["risk_metrics"] = risk_metrics
    overview = build_evidence_overview(per_fund)
    if overview.get("available"):
        facts["evidence_overview"] = overview
    if budget_enhancements:
        facts["market_flow"] = market_flow or _market_flow_unavailable("timeout")
    else:
        facts["market_flow"] = build_market_flow_context(
            trade_date=effective_trade_date,
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
