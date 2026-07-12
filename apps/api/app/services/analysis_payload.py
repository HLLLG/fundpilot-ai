from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.request_context import try_get_request_user_id
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
from app.services.news_freshness import build_news_pipeline_context
from app.services.pipeline_concurrency import run_with_request_user
from app.services.portfolio_snapshot import (
    build_factor_scores_for_facts,
    build_portfolio_trend_context,
    build_risk_metrics_for_facts,
)
from app.services.trading_session import build_trading_session
from app.services.decision_data_evidence import attach_analysis_data_evidence

AnalysisPayloadPhase = Literal[1, 2, 3]

FACTOR_SCORE_TIMEOUT_SECONDS = 4.0
RISK_METRICS_TIMEOUT_SECONDS = 3.0

# 迁入 system 的完整输出约束（不再每条请求在 user JSON 重复）
OUTPUT_REQUIREMENTS_SYSTEM = (
    "analysis_facts.portfolio_position_truth 是持仓份额、成本和现金的唯一真值摘要；"
    "unknown/null 不得按 0 猜测；position_complete=false、ledger_truncated=true 或存在 "
    "pending/conflict 时，amount_yuan 必须为 null，且不得生成任何可执行仓位金额。"
    "输出必须是完整 JSON（不要 Markdown），包含 title、summary、fund_recommendations、caveats。"
    "fund_recommendations 每只持仓基金恰好 1 条；字段含 fund_code、fund_name、action、"
    "amount_yuan（可选）、amount_note（可选）、news_bullish、news_bearish、points（1-3 条，每条≤60字）、"
    "confidence（高/中/低）、decision_path、sector_evidence、fund_evidence、validation_notes、"
    "hold_horizon（可选）、risks（至少 1 条）。"
    "decision_path 为 1 句话，须按「先看该持仓板块方向(sector_opportunity/sector_rotation)→"
    "再看该基金自身证据(evidence/factor_scores/risk_metrics)→最后给出动作」组织；"
    "sector_evidence 引用 sector_opportunity 的 track/confidence/pattern_label 或 sector_rotation.market_top；"
    "fund_evidence 引用 evidence、factor_scores、risk_metrics 等具体字段；"
    "validation_notes 写明证据不足/样本有限/信息缺口，无问题则 []；"
    "以上字段缺失时后端会兜底补全，但能给出真实依据时必须给，不得编造未提供的数字。"
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
    "sector_momentum/sector_intraday/sector_fund_flow 为短线提示；stock_connect_flow 仅提供南向数值，"
    "北向只保留 not_disclosed 审计状态、不得用于决策；南向仅作港股资金面的独立参考。"
    "sector_fund_flow.pattern_hint 可辅助判断高位出货、低位洗盘等，须用给定数字不得编造。"
    "sector_fund_flow.today_main_force_net_yi：正=主力净流入、负=主力净流出；"
    "须与 flow_date 同日且 date_aligned=true 时才可与 sector_return_percent 做量价背离判断；"
    "date_aligned=false 或 pattern_label=flow_date_mismatch 时禁止写出货/诱多等背离结论。"
    "sector_fund_flow.flow_tiers 为「今日」资金分档净流入（单位：亿元）："
    "super_large_net_yi=超大单(机构)、large_net_yi=大单、medium_net_yi=中单(大户)、"
    "small_net_yi=小单(散户)；flow_structure_hint 已系统解读机构与散户资金是否同向，"
    "可直接引用其结论，不得凭空推断未给出的机构/散户资金动向。"
    "analysis_facts.holdings[].sector_opportunity 是该持仓板块的方向判断（track顺势/蓄势，"
    "confidence高中低不足）：opportunity_available=false 表示当前不构成机会（如资金持续流出、"
    "涨幅透支），只能作为风险提示，不得作为加仓理由；true 时可作为继续持有的辅助论据之一。"
    "analysis_facts.sector_rotation.market_top 是当前更强的轮动方向参考，仅用于「是否存在更强"
    "方向」的提示，不得单独作为清仓已持仓位、追高换仓的理由，须结合该持仓自身证据综合判断。"
    "analysis_facts.news.freshness_label 须在 summary 或 caveats 体现对决策置信度的影响。"
    "analysis_facts.data_evidence 是字段级时点证据：freshness=stale/unavailable 或 confidence=none 的事实"
    "不得支撑动作；is_estimate=true 的数字必须明确写为估算并降低结论置信度。"
    "news_titles 中 source=cls 为财联社快讯。若 nav_trend 为空须在 points 说明。"
    "analysis_facts.market_breadth 是大盘情绪温度计（自上而下）：sentiment_level 基于全市场"
    "创新高低家数近2年历史分布百分位自校准，冰点/低迷代表市场情绪偏冷，可作为「即使板块"
    "本身尚未走弱，仍应降低追涨敏感度」的独立风险论据；涨跌停家数等仅为当日快照，不得"
    "当作历史回测结论使用。"
    "analysis_facts.holdings[].flow_divergence_backtest 是该持仓板块「量价背离」信号"
    "（涨但资金流出/跌但资金流入）的历史回测：by_rule 各桶含 trigger_count、hit_rate_percent、"
    "edge_percent、significant；significant=true 时可作为该持仓板块方向判断的量化背书之一，"
    "与 sector_opportunity/evidence 合并判断，不得单独作为加仓或减仓的唯一依据。"
)

OUTPUT_REQUIREMENTS_USER = [
    "portfolio_position_truth 中 unknown/null 不得按 0；position_complete=false、ledger_truncated=true 或存在 pending/conflict 时 amount_yuan 必须为空",
    "analysis_facts 为系统计算的只读事实，不得改写其中任何数字",
    "输出 title、summary、fund_recommendations、caveats；每只基金恰好 1 条 recommendation",
    "action 仅限 analysis_facts.allowed_actions；risk_review 或 high 禁止加仓类",
    "news_bullish/news_bearish 为字符串数组，须来自 news_titles 或 topic_briefs.points.source_titles",
    "每只基金 points 1-3 条：含权重/持有收益/净值或板块数据，且至少 1 条写下一交易日条件化预案",
    "引用 sector_intraday.pattern_label、nav_trend、sector_fund_gap_percent、sector_fund_flow 时须用 analysis_facts 中的数字",
    "每只基金须含 confidence、decision_path、sector_evidence、fund_evidence、validation_notes、risks（至少1条）",
    "decision_path 须体现「先判断板块方向(sector_opportunity)→再看基金自身证据→最后给出动作」的顺序",
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
    # Internal report orchestration evidence; never expose it to an LLM/public payload.
    trimmed.pop("sector_flow_by_label", None)
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
                            "trade_date",
                            "flow_date",
                            "date_aligned",
                            "today_main_force_net_yi",
                            "main_force_direction",
                            "cumulative_5d_net_yi",
                            "five_day_source",
                            "cumulative_20d_net_yi",
                            "flow_tiers",
                            "flow_structure_hint",
                            "pattern_label",
                            "pattern_hint",
                        )
                        if k in sector_flow
                    } or None
                else:
                    flow_copy = dict(sector_flow)
                    flow_copy.pop("message", None)
                    copy["sector_fund_flow"] = flow_copy
            sector_opportunity = copy.get("sector_opportunity")
            if isinstance(sector_opportunity, dict):
                opportunity_copy = {k: v for k, v in sector_opportunity.items() if k != "sector_group"}
                if analysis_mode == "fast" and phase >= 2:
                    opportunity_copy = {
                        k: opportunity_copy[k]
                        for k in (
                            "track",
                            "confidence",
                            "opportunity_available",
                            "entry_hint",
                            "pattern_label",
                            "today_main_force_net_yi",
                            "cumulative_5d_net_yi",
                            "today_available",
                            "five_day_available",
                            "five_day_source",
                            "history_point_count",
                        )
                        if k in opportunity_copy
                    }
                copy["sector_opportunity"] = opportunity_copy or None
            divergence = copy.get("flow_divergence_backtest")
            if isinstance(divergence, dict) and analysis_mode == "fast" and phase >= 2:
                # fast 模式只保留 LLM 真正会用到的字段：是否解析成功 + 各规则统计桶
                # （by_rule 内部结构本身已经很紧凑：trigger_count/hit_rate_percent/
                # baseline_rate_percent/edge_percent/significant）。
                copy["flow_divergence_backtest"] = {
                    "resolved": divergence.get("resolved"),
                    "by_rule": divergence.get("by_rule") or {},
                } or None
        holdings.append(copy)
    trimmed["holdings"] = holdings

    news = trimmed.get("news")
    if isinstance(news, dict) and phase >= 1:
        news_copy = {k: news[k] for k in news if k != "topics"}
        trimmed["news"] = news_copy

    rotation = trimmed.get("sector_rotation")
    if isinstance(rotation, dict) and phase >= 1:
        market_top = [
            {k: v for k, v in item.items() if k != "sector_group"}
            for item in (rotation.get("market_top") or [])
            if isinstance(item, dict)
        ]
        if analysis_mode == "fast" and phase >= 2:
            market_top = market_top[:3]
        trimmed["sector_rotation"] = {
            "available": rotation.get("available", False),
            "market_top": market_top,
        }

    if phase >= 2 and not is_short_term_style(decision_style):
        trimmed.pop("stock_connect_flow", None)
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

    # market_breadth 是自上而下的大盘情绪信号（用户此前踩坑的案例正是"板块微涨但大盘
    # 整体转冷"），与 stock_connect_flow/signal_backtest 偏短线定位不同，不因 decision_style
    # 是稳健模式而整体裁掉；fast 模式下仅保留 LLM 真正用得到的精简字段控制体积。
    if phase >= 2 and analysis_mode == "fast" and isinstance(trimmed.get("market_breadth"), dict):
        breadth = trimmed["market_breadth"]
        if breadth.get("available"):
            trimmed["market_breadth"] = {
                k: breadth[k]
                for k in ("available", "sentiment_level", "sentiment_level_change", "interpretation")
                if k in breadth
            }

    snapshot = trimmed.get("portfolio_snapshot")
    if isinstance(snapshot, dict):
        trimmed["portfolio_snapshot"] = {
            key: snapshot.get(key)
            for key in (
                "snapshot_id",
                "source",
                "authoritative",
                "as_of_date",
                "effective_trade_date",
                "client_snapshot_mismatch",
                "stale",
                "degraded",
                "freshness",
                "degradation_reason",
            )
        }
    evidence = trimmed.get("data_evidence")
    if isinstance(evidence, dict):
        trimmed["data_evidence"] = {
            "schema_version": evidence.get("schema_version"),
            "decision_ready": evidence.get("decision_ready"),
            "blocking_reasons": evidence.get("blocking_reasons") or [],
            "items": [
                {
                    key: item.get(key)
                    for key in (
                        "fact_id",
                        "source",
                        "source_type",
                        "as_of_date",
                        "available_at",
                        "fetched_at",
                        "freshness",
                        "confidence",
                        "is_estimate",
                    )
                }
                for item in (evidence.get("items") or [])
                if isinstance(item, dict)
            ],
        }

    return trimmed


@dataclass
class AnalysisFactsBundle:
    """一次计算的 analysis_facts 上下文，供 prompt 与存档复用。"""

    session: dict
    factor_scores: dict | None
    risk_metrics: dict | None
    portfolio_trend: dict | None
    facts: dict


def _enhancement_unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _run_budgeted_enhancement(
    func,
    *,
    timeout_seconds: float,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    user_id = try_get_request_user_id()

    def run():
        if user_id is None:
            return func()
        return run_with_request_user(user_id, func)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis-context-budget")
    future = executor.submit(run)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        future.cancel()
        return fallback
    except Exception:  # noqa: BLE001 - enhancement facts are best-effort
        return _enhancement_unavailable("error")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _compute_analysis_context(
    holdings: list,
    *,
    analysis_mode: AnalysisMode = "deep",
    phase: AnalysisPayloadPhase = 3,
    budget_enhancements: bool = False,
) -> tuple[dict, dict | None, dict | None, dict | None]:
    session = build_trading_session()
    include_portfolio_trend = not (phase >= 2 and analysis_mode == "fast")
    try:
        if budget_enhancements:
            factor_scores = _run_budgeted_enhancement(
                lambda: build_factor_scores_for_facts(holdings),
                timeout_seconds=FACTOR_SCORE_TIMEOUT_SECONDS,
                fallback=_enhancement_unavailable("timeout"),
            )
        else:
            factor_scores = build_factor_scores_for_facts(holdings)
    except Exception:  # noqa: BLE001 — best-effort，绝不阻塞日报
        factor_scores = None

    history_rows = None
    portfolio_trend = None
    risk_metrics = None
    try:
        from app.database import list_portfolio_daily_snapshots

        history_rows = list_portfolio_daily_snapshots(limit=400)
        if include_portfolio_trend:
            portfolio_trend = build_portfolio_trend_context(history_rows=history_rows)
        if budget_enhancements:
            risk_metrics = _run_budgeted_enhancement(
                lambda: build_risk_metrics_for_facts(history_rows, holdings),
                timeout_seconds=RISK_METRICS_TIMEOUT_SECONDS,
                fallback=_enhancement_unavailable("timeout"),
            )
        else:
            risk_metrics = build_risk_metrics_for_facts(history_rows, holdings)
    except Exception:  # noqa: BLE001 — best-effort，绝不阻塞日报
        if include_portfolio_trend and portfolio_trend is None:
            portfolio_trend = build_portfolio_trend_context()

    return session, factor_scores, risk_metrics, portfolio_trend


def prepare_analysis_bundle(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    prefetched_news: list[NewsItem],
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
    *,
    analysis_mode: AnalysisMode = "deep",
    phase: AnalysisPayloadPhase = 3,
    budget_enhancements: bool = False,
) -> AnalysisFactsBundle:
    """构建完整 analysis_facts（未 trim），供 LLM prompt 与最终存档各用一次。"""
    briefs = topic_briefs or []
    nav_trends = nav_trends_by_code or {}
    session, factor_scores, risk_metrics, portfolio_trend = _compute_analysis_context(
        request.holdings,
        analysis_mode=analysis_mode,
        phase=phase,
        budget_enhancements=budget_enhancements,
    )
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
        budget_enhancements=budget_enhancements,
    )
    facts = attach_analysis_data_evidence(
        facts,
        holdings=request.holdings,
        snapshots=snapshots,
        portfolio_context=request.portfolio_snapshot_context,
    )
    return AnalysisFactsBundle(
        session=session,
        factor_scores=factor_scores,
        risk_metrics=risk_metrics,
        portfolio_trend=portfolio_trend,
        facts=facts,
    )


def finalize_analysis_facts(
    base_facts: dict,
    *,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    pipeline: dict | None = None,
) -> dict:
    """在预计算 facts 上叠加 pipeline / 更新后的 news，避免重复 build_analysis_facts。"""
    facts = dict(base_facts)
    if market_news is not None or topic_briefs is not None:
        facts["news"] = build_news_pipeline_context(market_news or [], topic_briefs)
    if pipeline is not None:
        facts["pipeline"] = pipeline
    return facts


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
    analysis_bundle: AnalysisFactsBundle | None = None,
    operator_notes: list[str] | None = None,
) -> dict:
    briefs = topic_briefs or []
    bundle = analysis_bundle or prepare_analysis_bundle(
        request,
        risk,
        snapshots,
        prefetched_news,
        briefs,
        nav_trends_by_code,
        analysis_mode=analysis_mode,
        phase=phase,
    )
    facts = trim_analysis_facts_for_llm(
        bundle.facts,
        analysis_mode=analysis_mode,
        decision_style=request.profile.decision_style or "conservative",
        phase=phase,
    )

    minimal_briefs = phase >= 2 and analysis_mode == "fast"
    payload: dict = {
        "today": datetime.now().date().isoformat(),
        "profile": slim_profile_for_llm(request.profile),
        "holding_return_semantics": HOLDING_RETURN_SEMANTICS,
        "analysis_facts": facts,
        "news_titles": compact_news_titles(prefetched_news, briefs),
        "topic_briefs": compact_topic_briefs(briefs, minimal=minimal_briefs),
        "requirements": list(OUTPUT_REQUIREMENTS_USER),
    }
    if operator_notes:
        payload["operator_notes"] = list(operator_notes)
    return payload


def append_output_requirements_to_system(system_prompt: str) -> str:
    return system_prompt + OUTPUT_REQUIREMENTS_SYSTEM
