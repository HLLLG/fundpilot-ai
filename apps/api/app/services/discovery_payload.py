from __future__ import annotations

from datetime import date

from app.models import InvestorProfile, NewsItem, TopicBrief
from app.services.analysis_payload import compact_news_titles, compact_topic_briefs
from app.services.analysis_runtime import AnalysisMode

OUTPUT_DISCOVERY_REQUIREMENTS = """
你必须只输出一个 JSON 对象（不要 Markdown 代码块），字段：
- title: 报告标题
- summary: 2-4 句市场与配置总结
- market_view: 对大盘/板块的简短看法
- recommendations: 数组，3~5 项；每项含 fund_code, fund_name, sector_name, action,
  suggested_amount_yuan, amount_note, hold_horizon, confidence, points, risks, news_bullish
- caveats: 字符串数组，须含风险提示

约束：
- fund_code 必须来自 user 消息中 candidate_pool，禁止编造
- action 仅用：建议关注、分批买入、等待回调
- confidence 仅用：高、中、低
- hold_horizon 示例：2-4周、1-3个月、3-6个月
- 不得承诺收益；示意金额须结合 available_budget_yuan 与 concentration_limit_percent
"""

_FULL_MARKET_REQUIREMENTS = [
    "仅从 candidate_pool 选 3~5 只",
    "基于 sector_heat 与 target_sector_context 全市场横向对比",
    "portfolio_gap/已持仓板块仅作背景参考，不要以「持仓缺口」为主叙事框架",
    "market_view 须覆盖当日热度靠前板块与相对冷门但有机会的方向",
    "每只须含 hold_horizon 与 risks",
    "news_bullish 仅引用 news_titles 或 topic_briefs.points.source_titles 中已有标题",
    "不得推荐用户已持有基金（见 portfolio_gap）",
    "须在 summary 或 caveats 体现 news.freshness_label 对置信度的影响",
    "引用北向/南向资金须用 market_flow；板块主力须用 target_sector_context.sector_fund_flow",
    "signal_backtest 按 confidence.level：高可作主理由，低/不足仅提示",
]

_GAP_REQUIREMENTS = [
    "仅从 candidate_pool 选 3~5 只",
    "结合 portfolio_gap 解释为何关注该板块/基金（缺口补全视角）",
    "每只须含 hold_horizon 与 risks",
    "news_bullish 仅引用 news_titles 或 topic_briefs.points.source_titles 中已有标题",
    "不得推荐用户已持有基金（见 portfolio_gap）",
    "须在 summary 或 caveats 体现 news.freshness_label",
    "引用资金流/信号回测须用 discovery_facts 内数字，禁止编造",
]


def build_user_payload(
    *,
    discovery_facts: dict,
    profile: InvestorProfile,
    focus_sectors: list[str],
    scan_mode: str = "full_market",
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    analysis_mode: AnalysisMode = "fast",
) -> dict:
    pool = discovery_facts.get("candidate_pool") or []
    slim_pool = []
    for item in pool:
        slim_pool.append(
            {
                "fund_code": item.get("fund_code"),
                "fund_name": item.get("fund_name"),
                "sector_label": item.get("sector_label"),
                "return_1y_percent": item.get("return_1y_percent"),
                "max_drawdown_1y_percent": item.get("max_drawdown_1y_percent"),
                "fund_scale_yi": item.get("fund_scale_yi"),
                "selection_reason": item.get("selection_reason"),
            }
        )
    requirements = _FULL_MARKET_REQUIREMENTS if scan_mode == "full_market" else _GAP_REQUIREMENTS
    briefs = topic_briefs or []
    news = market_news or []
    minimal_briefs = analysis_mode == "fast"
    return {
        "today": date.today().isoformat(),
        "focus_sectors": focus_sectors,
        "scan_mode": scan_mode,
        "profile": discovery_facts.get("profile") or profile.model_dump(mode="json"),
        "news_titles": compact_news_titles(news, briefs),
        "topic_briefs": compact_topic_briefs(briefs, minimal=minimal_briefs),
        "discovery_facts": {
            "readonly": discovery_facts.get("readonly"),
            "instruction": discovery_facts.get("instruction"),
            "session": discovery_facts.get("session"),
            "portfolio_gap": discovery_facts.get("portfolio_gap"),
            "sector_heat": discovery_facts.get("sector_heat"),
            "target_sector_context": discovery_facts.get("target_sector_context"),
            "market_flow": discovery_facts.get("market_flow"),
            "signal_backtest": discovery_facts.get("signal_backtest"),
            "news": discovery_facts.get("news"),
            "candidate_factor_scores": discovery_facts.get("candidate_factor_scores"),
            "selection_strategy": discovery_facts.get("selection_strategy"),
            "dip_swing": discovery_facts.get("dip_swing"),
            "candidate_pool": slim_pool,
        },
        "requirements": requirements,
    }


def append_output_requirements_to_system(system_prompt: str) -> str:
    return system_prompt.rstrip() + "\n\n" + OUTPUT_DISCOVERY_REQUIREMENTS.strip()
