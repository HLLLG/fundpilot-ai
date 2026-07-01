from __future__ import annotations

from datetime import date

from app.models import InvestorProfile, NewsItem, TopicBrief
from app.services.analysis_payload import compact_news_titles, compact_topic_briefs
from app.services.analysis_runtime import AnalysisMode
from app.services.discovery_candidate_llm import (
    build_sector_change_index,
    slim_candidate_for_llm,
    trim_sector_heat_for_llm,
)

OUTPUT_DISCOVERY_REQUIREMENTS = """
你必须只输出一个 JSON 对象（不要 Markdown 代码块），字段：
- title: 报告标题
- summary: 2-4 句市场与配置总结
- market_view: 对大盘/板块的简短看法
- recommendations: 数组，3~5 项；每项含 fund_code, fund_name, sector_name, action,
  suggested_amount_yuan, amount_note, hold_horizon, confidence, decision_path,
  sector_evidence, fund_evidence, validation_notes, points, risks, news_bullish
- caveats: 字符串数组，须含风险提示

recommendations 字段约束：
- fund_code / fund_name 必须与 discovery_facts.candidate_pool 对应条目完全一致
- sector_name 须与 candidate_pool 中该基金的 sector_label 一致
- action 仅用：建议关注、分批买入、等待回调
- confidence 仅用：高、中、低
- hold_horizon 示例：2-4周、1-3个月、3-6个月；dip_swing 模式可用 2-5天
- decision_path: 1 句话，必须按「先判断板块方向 → 再比较方向内候选基金质量 → 最后决定动作」说明
- sector_evidence: 字符串数组，引用 sector_opportunities 中的 score、track、confidence、资金流、pattern；
  若没有对应 sector_opportunities，须说明使用 sector_heat / target_sector_context 降级判断
- fund_evidence: 字符串数组，引用 candidate_pool 中的 fund_quality_score、sector_fit_score、
  quality_reasons、return_3m_percent/return_6m_percent、max_drawdown_1y_percent、fund_scale_yi
- validation_notes: 字符串数组，写清 quality_penalties、信息缺失、追高风险、新闻 stale/empty 等校验备注；无明显问题则 []
- points: 字符串数组，每条须引用 candidate_pool 内具体字段（如 nav_trend、return_3m_percent、
  estimated_daily_return_percent、sector_fund_flow）；daily_return_source=sector_estimate 时须写「估算」
- risks: 字符串数组，每只至少 1 条
- news_bullish: 字符串数组，仅引用 news_titles 或 topic_briefs.points.source_titles 中已有标题；无则 []
- suggested_amount_yuan: 正数；须结合 portfolio_gap.available_budget_yuan 与 profile.concentration_limit_percent，
  单只示意金额不得超过可投入预算，且须说明与现有 holdings_slim 同板块合计不超限的理由（amount_note 中体现）
- 面向用户展示时必须使用中文标签，不要原样输出 fund_quality_score、sector_fit_score、quality_penalties、
  sector_opportunities、nav_trend、max_drawdown_1y_percent、estimated_daily_return_percent 等内部字段名；
  可写成“基金质量分”“板块匹配分”“系统校验提示”“系统筛出的主方向”“净值走势”“近1年最大回撤”“今日涨跌估算”等。

全局约束：
- 不得推荐 portfolio_gap.holdings_slim 中已持有的 fund_code
- 不得承诺收益；不得编造 candidate_pool 外的代码或未提供的估值分位
- full_market 模式须先判断板块方向，再在方向内选基金；不得只按基金近1年收益排序
- 引用北向/南向用 market_flow；板块主力用 target_sector_context.sector_fund_flow
- sector_opportunities 是系统已用 1d/5d 涨跌 + 今日/5日主力资金 + pattern 生成的主方向，
  推荐理由须优先引用它，而不是重新发明方向
- signal_backtest / candidate_factor_scores 按 confidence.level / factor_reliability 表述
- summary 或 caveats 须体现 news.freshness_label 对置信度的影响
- 新闻由系统预取并已做时效筛选；不得引用 news_titles/topic_briefs 之外的新闻，
  news.freshness_label 为 stale/empty/aging 时，新闻只能作背景，不能作为买入或追涨主依据
"""

_COMMON_REQUIREMENTS = [
    "仅从 discovery_facts.candidate_pool 选 3~5 只，不得推荐 holdings_slim 中已有 fund_code",
    "每只 recommendations 须含 hold_horizon、risks（至少 1 条）、points（引用 candidate_pool 具体字段）",
    "每只 recommendations 须含 decision_path、sector_evidence、fund_evidence、validation_notes",
    "先判断板块方向，再比较方向内基金质量分，最后决定动作",
    "展示文本使用中文标签，不要原样输出 fund_quality_score/sector_fit_score/quality_penalties 等内部字段名",
    "estimated_daily_return_percent 且 daily_return_source=sector_estimate 时，points 须注明「估算」",
    "判断追高风险须参考 nav_trend.distance_from_high_percent / trend_label，不得只看 sector_heat",
    "news_bullish 仅引用 news_titles 或 topic_briefs.points.source_titles；无匹配则 []",
    "新闻仅使用系统预取的 news_titles/topic_briefs；过旧或为空的新闻不能作为买入主依据",
    "suggested_amount_yuan 须结合 available_budget_yuan 与 concentration_limit_percent",
    "引用数字须来自 discovery_facts，禁止编造",
]

_FULL_MARKET_REQUIREMENTS = [
    *_COMMON_REQUIREMENTS,
    "基于 sector_heat 与 target_sector_context 做全市场横向对比",
    "先判断板块方向（sector_opportunities/target_sector_context），再比较方向内基金质量分，最后决定动作",
    "sector_opportunities 是系统已用 1d/5d 涨跌 + 今日/5日主力资金 + pattern 生成的主方向，推荐理由须优先引用它",
    "portfolio_gap / holdings_slim 仅作背景，不要以「持仓缺口」为主叙事",
    "market_view 须覆盖热度靠前板块与相对冷门但有机会的方向",
    "引用北向/南向须用 market_flow；板块主力须用 target_sector_context.sector_fund_flow",
]

_GAP_REQUIREMENTS = [
    *_COMMON_REQUIREMENTS,
    "结合 portfolio_gap（含 holdings_slim 的 sector_name、weight_percent）解释缺口补全理由",
    "优先推荐 holdings_slim 中未重仓、sector_heat 靠前的板块候选",
    "同 sector_name 合计权重不得超过 concentration_limit_percent，须在 amount_note 说明",
]

_DIP_SWING_REQUIREMENTS = [
    *_COMMON_REQUIREMENTS,
    "须结合 dip_drop_percent、nav_trend、dip_swing.fee_break_even_percent 说明 2~5 天短线窗口",
    "强调大跌≠利好；优先 分批买入 / 建议关注 / 等待回调，避免重仓抄底措辞",
    "hold_horizon 建议 2-5天；须提示达 fee_break_even_percent 考虑止盈",
]


def _requirements_for_scan_mode(scan_mode: str) -> list[str]:
    normalized = scan_mode if scan_mode != "gap" else "portfolio_gap"
    if normalized == "full_market":
        return _FULL_MARKET_REQUIREMENTS
    if normalized == "dip_swing":
        return _DIP_SWING_REQUIREMENTS
    return _GAP_REQUIREMENTS


def build_user_payload(
    *,
    discovery_facts: dict,
    profile: InvestorProfile,
    focus_sectors: list[str],
    scan_mode: str = "full_market",
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    analysis_mode: AnalysisMode = "fast",
    fund_type_preference: str | None = None,
) -> dict:
    pool = discovery_facts.get("candidate_pool") or []
    session = discovery_facts.get("session") or {}
    trade_date = session.get("effective_trade_date")
    sector_heat_full = discovery_facts.get("sector_heat") or []
    sector_change_index = build_sector_change_index(sector_heat_full)
    portfolio_gap = discovery_facts.get("portfolio_gap") or {}
    target_sectors = list(portfolio_gap.get("target_sectors") or [])
    slim_pool = [
        slim_candidate_for_llm(
            item,
            sector_change_index=sector_change_index,
            trade_date=trade_date,
        )
        for item in pool
    ]
    trimmed_heat = trim_sector_heat_for_llm(
        sector_heat_full,
        target_sectors=target_sectors,
        focus_sectors=focus_sectors,
    )
    resolved_fund_type = fund_type_preference or discovery_facts.get("fund_type_preference") or "any"
    requirements = _requirements_for_scan_mode(scan_mode)
    briefs = topic_briefs or []
    news = market_news or []
    minimal_briefs = analysis_mode == "fast"
    return {
        "today": date.today().isoformat(),
        "focus_sectors": focus_sectors,
        "scan_mode": scan_mode,
        "fund_type_preference": resolved_fund_type,
        "profile": discovery_facts.get("profile") or profile.model_dump(mode="json"),
        "news_titles": compact_news_titles(news, briefs),
        "topic_briefs": compact_topic_briefs(briefs, minimal=minimal_briefs),
        "discovery_facts": {
            "readonly": discovery_facts.get("readonly"),
            "instruction": discovery_facts.get("instruction"),
            "session": discovery_facts.get("session"),
            "portfolio_gap": portfolio_gap,
            "fund_type_preference": resolved_fund_type,
            "sector_heat": trimmed_heat,
            "target_sector_context": discovery_facts.get("target_sector_context"),
            "market_flow": discovery_facts.get("market_flow"),
            "signal_backtest": discovery_facts.get("signal_backtest"),
            "sector_opportunities": _slim_sector_opportunities(
                discovery_facts.get("sector_opportunities") or []
            ),
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


def _slim_sector_opportunities(items: list[dict]) -> list[dict]:
    slimmed: list[dict] = []
    for item in items[:8]:
        row = {
            "sector_label": item.get("sector_label"),
            "track": item.get("track"),
            "score": item.get("score"),
            "confidence": item.get("confidence"),
            "entry_hint": item.get("entry_hint"),
            "evidence": item.get("evidence") or [],
            "penalties": item.get("penalties") or [],
            "change_1d_percent": item.get("change_1d_percent"),
            "change_5d_percent": item.get("change_5d_percent"),
            "today_main_force_net_yi": item.get("today_main_force_net_yi"),
            "cumulative_5d_net_yi": item.get("cumulative_5d_net_yi"),
            "pattern_label": item.get("pattern_label"),
        }
        slimmed.append(row)
    return slimmed
