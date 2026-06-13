"""Legacy vs slim LLM payload builders for A/B comparison."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import (
    AnalysisRequest,
    FundSnapshot,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.services.analysis_facts import build_analysis_facts
from app.services.holding_metrics import HOLDING_RETURN_SEMANTICS, holding_analysis_payload
from app.services.portfolio_snapshot import build_portfolio_trend_context
from app.services.trading_session import build_trading_session

LEGACY_OUTPUT_REQUIREMENTS_USER = [
    "analysis_facts 为系统计算的只读事实，不得改写其中任何数字",
    "输出 title、summary、fund_recommendations、caveats 四个字段",
    "fund_recommendations 每只基金恰好 1 条",
    "每条字段：fund_code、fund_name、action、amount_yuan（可选）、amount_note（可选）、"
    "news_bullish（利好标题数组）、news_bearish（利空/风险标题数组）、points（1-3 条，每条≤60字）",
    "优先依据 topic_briefs 理解板块与宏观背景；news_bullish/news_bearish 须来自 prefetched_news 标题"
    "或 topic_briefs.points.source_titles；无则写「暂无明确利好/利空」",
    "须遵循 session.decision_window 与 session.session_kind 调整措辞，"
    "非 trading_day_pre_close 时不要写「收盘前必须今日下单」",
    "收盘前决策：action 仅限 观察/暂停追涨/分批加仓/减仓评估/风控复核 五选一",
    "若 risk.suggested_action 为 risk_review 或 level 为 high，禁止给出加仓类 action",
    "涉及加仓/减仓须给 amount_yuan 或 amount_note（结合 holding_amount 与 concentration_limit_percent）",
    "recommendations 可省略或仅 1 条组合级说明，禁止长新闻摘要堆砌",
    "旧新闻仅作参考；判断当日涨跌优先 daily_return_percent，否则用 estimated_daily_return_percent",
    "引用当日涨跌时区分：板块 sector_return_percent、昨日结算 holding_return_percent、估算/实际当日收益",
    "基金代码 000000 须提示补全代码",
    "decision_style=tactical 时可更积极运用 sector_intraday/sector_momentum；conservative 时偏稳健、避免追涨",
    "不做实盘交易指令",
    "analysis_facts.holdings[].nav_trend 为近 N 交易日净值摘要（含 trend_label、距高点距离、recent_nav_series），"
    "用于判断反弹/回落与区间位置；不得编造未给出的净值序列",
    "analysis_facts.holdings[].sector_momentum 为短线模式提示（如 two_day_reversal_down 涨后回吐），"
    "须纳入当日与下一交易日预案",
    "analysis_facts.holdings[].sector_intraday 为板块分时形态（如 intraday_pullback 冲高回落）",
    "analysis_facts.market_flow 含北向资金 interpretation，战术模式须参考",
    "analysis_facts.news 含 freshness_label 与 interpretation，须在 summary 或 caveats 中体现对决策置信度的影响",
    "prefetched_news 中 source=cls 为财联社快讯，时效通常优于纯东财检索",
    "若 nav_trend 为空（如基金代码 000000），须在 points 中说明无法使用净值走势",
    "收盘前窗口：每只基金 points 至少 1 条写「下一交易日」条件化预案（如板块延续强势/冲高回落则如何动作）",
]


def build_legacy_user_payload(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    prefetched_news: list[NewsItem],
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
) -> dict:
    briefs = topic_briefs or []
    nav_trends = nav_trends_by_code or {}
    session = build_trading_session()
    facts = build_analysis_facts(
        request.holdings,
        risk,
        snapshots,
        request.profile,
        briefs,
        nav_trends,
        prefetched_news,
        session=session,
        portfolio_trend=build_portfolio_trend_context(),
        for_llm=False,
    )
    return {
        "today": datetime.now().date().isoformat(),
        "analysis_session": session["session_kind"],
        "session": session,
        "profile": request.profile.model_dump(),
        "holding_return_semantics": HOLDING_RETURN_SEMANTICS,
        "analysis_facts": facts,
        "holdings": [holding_analysis_payload(holding) for holding in request.holdings],
        "risk": risk.model_dump(),
        "fund_snapshots": [snapshot.model_dump() for snapshot in snapshots],
        "ocr_text": request.ocr_text,
        "prefetched_news": [item.model_dump() for item in prefetched_news],
        "topic_briefs": [item.model_dump(mode="json") for item in briefs],
        "requirements": list(LEGACY_OUTPUT_REQUIREMENTS_USER),
    }


def legacy_system_news_hint() -> str:
    return (
        "用户消息中 topic_briefs 为按主题预摘要（优先阅读），prefetched_news 为原始出处列表；"
        "利好/利空标题须能在 prefetched_news 或 topic_briefs.points.source_titles 中找到对应。"
        "优先采用当日新闻，前几日仅作背景并标注日期，避免用旧闻主导结论。"
        "如需补充可调用 fetch_market_news，但不要重复拉取已有主题。"
    )
