from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.models import (
    AnalysisRequest,
    FundSnapshot,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.services.investment_presets import is_short_term_style, take_profit_threshold_percent
from app.services.analysis_facts import build_analysis_facts
from app.services.holding_metrics import HOLDING_RETURN_SEMANTICS
from app.services.analysis_runtime import AnalysisMode
from app.services.portfolio_snapshot import build_portfolio_trend_context
from app.services.trading_session import build_trading_session

AnalysisPayloadPhase = Literal[1, 2, 3]

# 迁入 system 的完整输出约束（不再每条请求在 user JSON 重复）
OUTPUT_REQUIREMENTS_SYSTEM = (
    "输出必须是完整 JSON（不要 Markdown），包含 title、summary、fund_recommendations、caveats。"
    "fund_recommendations 每只持仓基金恰好 1 条；字段含 fund_code、fund_name、action、"
    "amount_yuan（可选）、amount_note（可选）、news_bullish、news_bearish、points（1-3 条，每条≤60字）。"
    "news_bullish 与 news_bearish 必须是字符串 JSON 数组（如 [\"标题\"]），禁止写成单个字符串；"
    "无则写 [\"暂无明确利好\"] 或 [\"暂无明确利空\"]。"
    "利好/利空标题须能在 news_titles 或 topic_briefs.points.source_titles 中找到对应。"
    "须遵循 analysis_facts.session.decision_window 与 session_kind 调整措辞，"
    "非 trading_day_pre_close 时不要写「收盘前必须今日下单」。"
    "收盘前决策 action 仅限 analysis_facts.allowed_actions 五选一。"
    "若 analysis_facts.portfolio.suggested_action 为 risk_review 或 risk_level 为 high，禁止加仓类 action。"
    "涉及加仓/减仓须给 amount_yuan 或 amount_note（结合 holding_amount 与 concentration_limit_percent）。"
    "recommendations 可省略或仅 1 条组合级说明，禁止长新闻摘要堆砌。"
    "判断当日涨跌优先 daily_return_percent，否则用 sector_return_percent 估算；"
    "判断累计持有收益/浮亏须用 estimated_holding_return_percent（与界面「持有」列一致），"
    "勿用 holding_return_percent（昨日结算）。"
    "区分 sector_return_percent（板块）、holding_return_percent（昨日结算）、"
    "estimated_holding_return_percent（累计持有）、daily_return_percent（当日）。"
    "基金代码 000000 须提示补全代码。不做实盘交易指令。"
    "analysis_facts.holdings[].nav_trend 为净值摘要，不得编造未给出的序列；"
    "sector_momentum/sector_intraday/sector_fund_flow 为短线提示；market_flow 为北向资金解读（若提供）。"
    "sector_fund_flow.pattern_hint 可辅助判断高位出货、低位洗盘等，须用给定数字不得编造。"
    "analysis_facts.news.freshness_label 须在 summary 或 caveats 体现对决策置信度的影响。"
    "news_titles 中 source=cls 为财联社快讯。若 nav_trend 为空须在 points 说明。"
)

OUTPUT_REQUIREMENTS_USER = [
    "analysis_facts 为系统计算的只读事实，不得改写其中任何数字",
    "输出 title、summary、fund_recommendations、caveats；每只基金恰好 1 条 recommendation",
    "action 仅限 analysis_facts.allowed_actions；risk_review 或 high 禁止加仓类",
    "news_bullish/news_bearish 为字符串数组，须来自 news_titles 或 topic_briefs.points.source_titles",
    "每只基金 points 1-3 条：含权重/持有收益/净值或板块数据，且至少 1 条写下一交易日条件化预案",
    "引用 sector_intraday.pattern_label、nav_trend、sector_fund_gap_percent、sector_fund_flow 时须用 analysis_facts 中的数字",
]

_HOLDING_LLM_DROP_KEYS = frozenset(
    {
        "management_fee",
        "fund_scale_yi",
        "fund_type",
    }
)


def compact_news_titles(
    market_news: list[NewsItem],
    topic_briefs: list[TopicBrief] | None = None,
    *,
    today_only: bool = True,
    max_items: int = 20,
    min_items: int = 12,
) -> list[dict[str, Any]]:
    """仅保留标题级引用，供模型 cite；完整 NewsItem 仍留后端 news_citation 使用。

    优先当日新闻；若当日条数不足 min_items，用近几日标题补足（非交易日常见）。
    并合并 topic_briefs.points.source_titles，避免摘要中有、标题列表中无的引用缺口。
    """
    items: list[NewsItem] = list(market_news)
    if today_only:
        today_items = [item for item in items if item.is_today]
        other_items = [item for item in items if not item.is_today]
        if today_items:
            selected = list(today_items)
            if len(selected) < min_items:
                need = min_items - len(selected)
                selected.extend(other_items[:need])
            items = selected[:max_items]
        else:
            items = items[:max_items]
    else:
        items = items[:max_items]
    compact: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        title = item.title.strip()
        if not title or title in seen:
            continue
        seen.add(title)
        row: dict[str, Any] = {
            "topic": item.topic,
            "title": title,
            "is_today": item.is_today,
        }
        if item.published_at:
            row["published_at"] = item.published_at
        if item.source:
            row["source"] = item.source
        compact.append(row)

    for brief in topic_briefs or []:
        for point in brief.points:
            for raw_title in point.source_titles:
                title = str(raw_title).strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                compact.append(
                    {
                        "topic": brief.topic,
                        "title": title,
                        "is_today": point.is_today,
                        "from_brief": True,
                    }
                )
                if len(compact) >= max_items + 8:
                    break
    return compact[: max_items + 8]


def compact_topic_briefs(
    briefs: list[TopicBrief],
    *,
    minimal: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for brief in briefs:
        points: list[dict[str, Any]] = []
        for point in brief.points:
            entry: dict[str, Any] = {
                "headline": point.headline,
                "sentiment": point.sentiment,
                "is_today": point.is_today,
                "source_titles": list(point.source_titles),
            }
            if not minimal:
                entry["source_urls"] = list(point.source_urls)
            points.append(entry)
        payload: dict[str, Any] = {
            "topic": brief.topic,
            "summary": brief.summary,
            "points": points,
            "news_count": brief.news_count,
            "provider": brief.provider,
        }
        if not minimal and brief.summarized_at:
            payload["summarized_at"] = brief.summarized_at.isoformat()
        result.append(payload)
    return result


def slim_profile_for_llm(profile: InvestorProfile) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision_style": profile.decision_style,
        "prefer_dca": profile.prefer_dca,
        "avoid_chasing": profile.avoid_chasing,
        "max_drawdown_percent": profile.max_drawdown_percent,
        "concentration_limit_percent": profile.concentration_limit_percent,
        "expected_investment_amount": profile.expected_investment_amount,
    }
    if profile.decision_style == "aggressive":
        payload.update(
            {
                "round_trip_fee_percent": profile.round_trip_fee_percent,
                "min_net_profit_percent": profile.min_net_profit_percent,
                "take_profit_threshold_percent": take_profit_threshold_percent(profile),
                "hold_days_target": profile.hold_days_target,
            }
        )
    return payload


def trim_analysis_facts_for_llm(
    facts: dict[str, Any],
    *,
    analysis_mode: AnalysisMode = "deep",
    decision_style: str = "conservative",
    phase: AnalysisPayloadPhase = 3,
) -> dict[str, Any]:
    trimmed = dict(facts)
    holdings = []
    for row in facts.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        copy = dict(row)
        for key in _HOLDING_LLM_DROP_KEYS:
            copy.pop(key, None)
        if phase >= 2 and not is_short_term_style(decision_style):
            copy.pop("signal_backtest", None)
        if phase >= 1:
            nav = copy.get("nav_trend")
            if isinstance(nav, dict):
                nav_copy = dict(nav)
                nav_copy.pop("source", None)
                series = nav_copy.get("recent_nav_series")
                if isinstance(series, list) and len(series) > 5:
                    nav_copy["recent_nav_series"] = series[-5:]
                if phase < 3:
                    nav_copy.pop("recent_5d_daily_change_percent", None)
                copy["nav_trend"] = nav_copy
            intraday = copy.get("sector_intraday")
            if isinstance(intraday, dict) and phase >= 2:
                intraday_copy = {
                    k: intraday[k]
                    for k in (
                        "pattern_label",
                        "pattern_hint",
                        "close_change_percent",
                        "pullback_from_high_percent",
                    )
                    if k in intraday
                }
                copy["sector_intraday"] = intraday_copy or None
            sector_flow = copy.get("sector_fund_flow")
            if isinstance(sector_flow, dict) and phase >= 2:
                if analysis_mode == "fast":
                    copy["sector_fund_flow"] = {
                        k: sector_flow[k]
                        for k in (
                            "available",
                            "today_main_force_net_yi",
                            "cumulative_5d_net_yi",
                            "cumulative_20d_net_yi",
                            "pattern_label",
                            "pattern_hint",
                        )
                        if k in sector_flow
                    } or None
                else:
                    flow_copy = dict(sector_flow)
                    flow_copy.pop("message", None)
                    copy["sector_fund_flow"] = flow_copy
        holdings.append(copy)
    trimmed["holdings"] = holdings

    news = trimmed.get("news")
    if isinstance(news, dict) and phase >= 1:
        news_copy = {k: news[k] for k in news if k != "topics"}
        trimmed["news"] = news_copy

    if phase >= 2 and not is_short_term_style(decision_style):
        trimmed.pop("market_flow", None)
        trimmed.pop("signal_backtest", None)
        trimmed.pop("prompt_tuning", None)
        guard = trimmed.get("guard_policy")
        if isinstance(guard, dict):
            trimmed["guard_policy"] = {
                k: guard[k]
                for k in ("enforce_reversal_block", "enforce_pullback_block", "tighten_tactical", "reason")
                if k in guard
            }

    if phase >= 2 and analysis_mode == "fast":
        trimmed.pop("portfolio_trend", None)
    elif phase >= 2 and isinstance(trimmed.get("portfolio_trend"), dict):
        trend = trimmed["portfolio_trend"]
        if trend.get("has_history"):
            trimmed["portfolio_trend"] = {
                "has_history": True,
                "summary_line": trend.get("summary_line"),
            }
        else:
            trimmed.pop("portfolio_trend", None)

    if phase >= 2 and analysis_mode == "fast" and isinstance(trimmed.get("signal_backtest"), dict):
        backtest = trimmed["signal_backtest"]
        trimmed["signal_backtest"] = {
            "enabled": backtest.get("enabled"),
            "has_data": backtest.get("has_data"),
            "summary_lines": (backtest.get("summary_lines") or [])[:2],
        }

    return trimmed


def build_user_payload(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    prefetched_news: list[NewsItem],
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
    *,
    analysis_mode: AnalysisMode = "deep",
    phase: AnalysisPayloadPhase = 3,
) -> dict:
    briefs = topic_briefs or []
    nav_trends = nav_trends_by_code or {}
    session = build_trading_session()
    include_portfolio_trend = not (phase >= 2 and analysis_mode == "fast")
    try:
        from app.services.portfolio_snapshot import build_factor_scores_for_facts

        factor_scores = build_factor_scores_for_facts(request.holdings)
    except Exception:  # noqa: BLE001 — best-effort，绝不阻塞日报
        factor_scores = None

    # 历史快照 load 一次，供组合走势 + 风险度量复用
    history_rows = None
    portfolio_trend = None
    risk_metrics = None
    try:
        from app.database import list_portfolio_daily_snapshots
        from app.services.portfolio_snapshot import build_risk_metrics_for_facts

        history_rows = list_portfolio_daily_snapshots(limit=400)
        if include_portfolio_trend:
            portfolio_trend = build_portfolio_trend_context(history_rows=history_rows)
        risk_metrics = build_risk_metrics_for_facts(history_rows, request.holdings)
    except Exception:  # noqa: BLE001 — best-effort，绝不阻塞日报
        if include_portfolio_trend and portfolio_trend is None:
            portfolio_trend = build_portfolio_trend_context()

    facts = build_analysis_facts(
        request.holdings,
        risk,
        snapshots,
        request.profile,
        briefs,
        nav_trends,
        prefetched_news,
        session=session,
        portfolio_trend=portfolio_trend,
        factor_scores=factor_scores,
        risk_metrics=risk_metrics,
        for_llm=True,
    )
    facts = trim_analysis_facts_for_llm(
        facts,
        analysis_mode=analysis_mode,
        decision_style=request.profile.decision_style or "conservative",
        phase=phase,
    )

    minimal_briefs = phase >= 2 and analysis_mode == "fast"
    return {
        "today": datetime.now().date().isoformat(),
        "profile": slim_profile_for_llm(request.profile),
        "holding_return_semantics": HOLDING_RETURN_SEMANTICS,
        "analysis_facts": facts,
        "news_titles": compact_news_titles(prefetched_news, briefs),
        "topic_briefs": compact_topic_briefs(briefs, minimal=minimal_briefs),
        "requirements": list(OUTPUT_REQUIREMENTS_USER),
    }


def append_output_requirements_to_system(system_prompt: str) -> str:
    return system_prompt + OUTPUT_REQUIREMENTS_SYSTEM
