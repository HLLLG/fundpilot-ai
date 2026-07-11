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
from app.config import get_settings
from app.services.decision_guard_shared import (
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    resolve_escalation_floor,
)
from app.services.market_breadth_signal import build_market_breadth_signal
from app.services.market_flow_client import build_market_flow_context
from app.services.news_freshness import build_news_pipeline_context
from app.services.analysis_prompt import (
    COMPOSITE_EVIDENCE_INSTRUCTION,
    IC_EVIDENCE_INSTRUCTION,
)
from app.services.report_sector_opportunity import build_holding_sector_opportunity_context
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
from app.services.sector_labels import normalize_sector_label
from app.services.sector_quote_label import sector_quote_lookup_label

SIGNAL_BACKTEST_TIMEOUT_SECONDS = 5.0
SECTOR_INTRADAY_TIMEOUT_SECONDS = 4.0
MARKET_FLOW_TIMEOUT_SECONDS = 3.0
GUARD_POLICY_TIMEOUT_SECONDS = 2.0
SECTOR_OPPORTUNITY_TIMEOUT_SECONDS = 5.0
MARKET_BREADTH_TIMEOUT_SECONDS = 3.0

# M2.2：动作词表基础 5 档（始终出现）；「大幅减仓评估」「清仓评估」按 M2.1 触发矩阵
# 门槛动态追加——没有任一持仓触发对应档位时，prompt 里根本不出现这两个选项
# （设计原文："避免被滥用/误用吓退用户"）。
_BASE_ALLOWED_ACTIONS = ("观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核")
_ESCALATION_DEEP_REDUCE_THRESHOLD = ACTION_BUCKET_DEEP_REDUCE
_ESCALATION_CLEAR_ALL_THRESHOLD = ACTION_BUCKET_CLEAR_ALL


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


def _market_breadth_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "message": "大盘情绪温度计未在预算内完成，日报已按基础事实继续。",
    }


def _sector_opportunity_unavailable(
    reason: str,
    holdings: list[Holding] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "held": {},
        "market_top": [],
        "sector_flow_by_label": _sector_flow_unavailable_map(holdings or [], reason),
        "divergence_backtest": {},
    }


def _guard_policy_unavailable() -> dict[str, Any]:
    return {
        "enforce_reversal_block": True,
        "enforce_pullback_block": True,
        "tighten_tactical": False,
        "reason": "guard_policy_timeout",
        "backtest_summary_lines": [],
    }


def _attach_escalation_to_holdings(
    per_fund: list[dict],
    *,
    market_breadth: dict | None,
    profile: InvestorProfile,
) -> None:
    """给每个持仓行挂上 M2.1 的双向 guard 升级判定结果（key: `escalation`）。

    仅当该行同时具备 `sector_opportunity` 与 `evidence` 时才有意义调用——两者缺失时
    `resolve_escalation_floor` 本身已能优雅降级返回 `min_bucket=None`（详见该函数
    docstring），这里不做额外短路，保持单一判定入口。
    """
    for row in per_fund:
        row["escalation"] = resolve_escalation_floor(
            sector_opportunity=row.get("sector_opportunity"),
            evidence=row.get("evidence"),
            market_breadth=market_breadth,
            over_concentration=bool(row.get("over_concentration")),
            has_unrealized_gain=(row.get("estimated_holding_return_percent") or 0) > 0,
            decision_style=profile.decision_style,
        )


def _extra_allowed_actions_for_escalation(per_fund: list[dict]) -> list[str]:
    """按各持仓的 `escalation.min_bucket` 判断是否需要向 `allowed_actions` 追加
    「大幅减仓评估」「清仓评估」两个新动作词（M2.2）。

    M6：shadow 灰度期间恒返回空列表——这两个词本身就是本次升级要灰度验证的机制
    之一，如果 shadow 模式下仍然把它们递给模型选，模型选中后 recommendation_guard.py
    虽然不会强制生效（见该文件的 shadow 分支），但也不应该让模型在草案阶段就看到、
    选择这两个新词——灰度观察期的产品意图是"系统内部安静地算、只旁注提示"，不是
    "开放新选项但事后不生效"。拆成独立函数是为了让 shadow 门控可以脱离完整
    `build_analysis_facts` 调用链单独测试（原逻辑内联在函数体内、依赖只有在真实
    facts 组装流程中才会被填充的字段，难以在单测里精确构造）。
    """
    if get_settings().decision_escalation_mode != "enforced":
        return []
    if any(
        (row.get("escalation") or {}).get("min_bucket") is not None
        and row["escalation"]["min_bucket"] <= _ESCALATION_CLEAR_ALL_THRESHOLD
        for row in per_fund
    ):
        return ["清仓评估", "大幅减仓评估"]
    if any(
        (row.get("escalation") or {}).get("min_bucket") is not None
        and row["escalation"]["min_bucket"] <= _ESCALATION_DEEP_REDUCE_THRESHOLD
        for row in per_fund
    ):
        return ["大幅减仓评估"]
    return []


def _sector_flow_unavailable_map(
    holdings: list[Holding],
    reason: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label or label in result:
            continue
        result[label] = {
            "available": False,
            "sector_label": label,
            "reason": reason,
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
    market_breadth = None
    if budget_enhancements:
        executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="analysis-facts-budget")
        try:
            signal_future = _submit_enhancement(
                executor,
                lambda: build_signal_backtest_context(sector_labels),
            )
            guard_future = _submit_enhancement(
                executor,
                lambda: resolve_signal_guard_policy(holdings),
            )
            intraday_future = _submit_enhancement(
                executor,
                lambda: _build_sector_intraday_map(holdings),
            )
            market_flow_future = _submit_enhancement(
                executor,
                lambda: build_market_flow_context(trade_date=effective_trade_date),
            )
            sector_opportunity_future = _submit_enhancement(
                executor,
                lambda: build_holding_sector_opportunity_context(
                    holdings,
                    trade_date=effective_trade_date,
                ),
            )
            market_breadth_future = _submit_enhancement(
                executor,
                lambda: build_market_breadth_signal(effective_trade_date),
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
            sector_opportunity = _enhancement_result(
                sector_opportunity_future,
                timeout_seconds=SECTOR_OPPORTUNITY_TIMEOUT_SECONDS,
                fallback=_sector_opportunity_unavailable("timeout", holdings),
            )
            market_breadth = _enhancement_result(
                market_breadth_future,
                timeout_seconds=MARKET_BREADTH_TIMEOUT_SECONDS,
                fallback=_market_breadth_unavailable("timeout"),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    else:
        signal_backtest = build_signal_backtest_context(sector_labels)
        guard_policy = resolve_signal_guard_policy(holdings)
        intraday_map = _build_sector_intraday_map(holdings)
        try:
            sector_opportunity = build_holding_sector_opportunity_context(
                holdings,
                trade_date=effective_trade_date,
            )
        except Exception:  # noqa: BLE001 - opportunity/flow evidence is best-effort
            sector_opportunity = _sector_opportunity_unavailable("error", holdings)

    if not isinstance(sector_opportunity, dict):
        sector_opportunity = _sector_opportunity_unavailable("unavailable", holdings)
    raw_sector_flow_map = sector_opportunity.get("sector_flow_by_label")
    flow_fallback_reason = str(sector_opportunity.get("reason") or "unavailable")
    sector_flow_map = _sector_flow_unavailable_map(holdings, flow_fallback_reason)
    if isinstance(raw_sector_flow_map, dict):
        sector_flow_map.update(
            {
                label: flow
                for label, flow in raw_sector_flow_map.items()
                if isinstance(label, str) and isinstance(flow, dict)
            }
        )

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
                "sector_fund_flow": sector_flow_map.get(
                    normalize_sector_label(holding.sector_name)
                ),
                "signal_backtest": signal_backtest_for_sector(
                    holding.sector_name,
                    signal_backtest,
                ),
                "sector_opportunity": (sector_opportunity.get("held") or {}).get(
                    normalize_sector_label(holding.sector_name)
                ),
                "flow_divergence_backtest": (sector_opportunity.get("divergence_backtest") or {}).get(
                    normalize_sector_label(holding.sector_name)
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
            "组合风险指标(risk_metrics：夏普/回撤/Beta/HHI)为系统计算事实，"
            "按 confidence.level 表述：「高/中」可作风险论据；"
            "「低/不足」须声明样本有限、不得据此下强结论。"
            f"{IC_EVIDENCE_INSTRUCTION}"
            f"{COMPOSITE_EVIDENCE_INSTRUCTION}"
            "evidence_overview 是组合级量化背书体检：backed_weight_percent 为"
            "「中/高背书」市值占比；占比高→建议可更积极，占比低→须强调多数仓位"
            "量化背书不足、以风险口径表述。"
            "sector_fund_flow.today_main_force_net_yi 正数=净流入、负数=净流出；"
            "仅当 flow_date 与 trade_date 对齐（date_aligned=true）时方可与 sector_return_percent 做背离判断。"
            "持仓的 sector_opportunity 是该持仓所属板块当前方向判断（track=momentum顺势/setup蓄势，"
            "confidence=高/中/低/不足）：opportunity_available=false 表示该方向当前不构成机会"
            "（例如资金持续流出、涨幅透支），须在分析中提示、不得据此建议加仓；"
            "为 true 时可作为「继续持有/适度加仓」的辅助论据，但仍需结合 evidence 与风险指标。"
            "sector_rotation.market_top 是当前全市场机会分最高的方向（不含已持有板块），"
            "仅用于提示「是否存在更强的轮动方向」，不得单独作为清仓已持仓位、追高换仓的理由。"
            "market_breadth 是大盘情绪温度计：sentiment_level（冰点/低迷/中性/偏热/亢奋）基于"
            "全市场创新高低家数近2年历史分布百分位自校准，可作为自上而下的风险论据；"
            "limit_up_count/limit_down_count/limit_up_broken_ratio_percent 仅为当日快照，"
            "不是历史回测结论，只能作辅助描述、不得单独据此下强结论。"
            "持仓的 flow_divergence_backtest 是该持仓板块「量价背离」信号的历史回测（区别于"
            "sector_fund_flow 的定性提示）：按各规则 significant 与 edge_percent 表述，"
            "significant=true 且 edge_percent 越高，可信度越高；未显著或触发次数不足时"
            "只能作提示，不得主导追涨或减仓建议。"
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
        "allowed_actions": list(_BASE_ALLOWED_ACTIONS),
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
        facts["market_breadth"] = market_breadth or _market_breadth_unavailable("timeout")
    else:
        facts["market_flow"] = build_market_flow_context(
            trade_date=effective_trade_date,
        )
        facts["market_breadth"] = build_market_breadth_signal(effective_trade_date)
    # M2.1/M2.2：双向 guard 升级判定——须在 market_breadth 就位后才能算，因此放在这里
    # 而不是 per_fund 主循环内（非 budget_enhancements 路径下 market_breadth 变量在
    # 循环执行时尚未赋值，只有 facts["market_breadth"] 在此处才是最终值）。
    _attach_escalation_to_holdings(per_fund, market_breadth=facts["market_breadth"], profile=profile)
    facts["allowed_actions"].extend(_extra_allowed_actions_for_escalation(per_fund))
    facts["signal_backtest"] = signal_backtest
    facts["sector_rotation"] = {
        "available": sector_opportunity.get("available", False),
        "reason": sector_opportunity.get("reason"),
        "market_top": sector_opportunity.get("market_top", []),
    }
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
