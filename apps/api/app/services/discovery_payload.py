from __future__ import annotations

from app.models import InvestorProfile, NewsItem, TopicBrief
from app.services.analysis_payload import (
    compact_data_evidence_for_llm,
    compact_news_titles,
    compact_portfolio_position_truth_for_llm,
    compact_portfolio_snapshot_for_llm,
    compact_topic_briefs,
)
from app.services.analysis_runtime import AnalysisMode
from app.services.discovery_candidate_llm import (
    slim_candidate_pool_for_llm,
    trim_sector_heat_for_llm,
)
from app.services.news_freshness import normalize_news_now
from app.services.news_service import compact_announcement_fetch_status

OUTPUT_DISCOVERY_REQUIREMENTS = """
你必须只输出一个 JSON 对象（不要 Markdown 代码块），字段：
- title: 报告标题
- summary: 2-4 句市场与配置总结
- market_view: 对大盘/板块的简短看法
- recommendations: 数组，0~3 项；没有合格候选时允许为空；每项含 fund_code, fund_name, sector_name, action,
  suggested_amount_yuan, amount_note, hold_horizon, confidence, decision_path,
  sector_evidence, fund_evidence, validation_notes, points, risks, news_bullish
- caveats: 字符串数组，须含风险提示

recommendations 字段约束：
- fund_code / fund_name 必须与 discovery_facts.candidate_pool 对应条目完全一致
- sector_name 须与 candidate_pool 中该基金的 sector_label 一致
- action 仅用：建议关注、分批买入、等待回调
- confidence 仅用：高、中、低
- hold_horizon 示例：2-4周、1-3个月、3-6个月
- decision_path: 1 句话，必须按「先判断板块方向 → 再比较方向内候选基金质量 → 最后决定动作」说明
- sector_evidence: 字符串数组，引用 sector_opportunities 中的 score、track、confidence、资金流、pattern；
  若没有对应 sector_opportunities，须说明使用 sector_heat / target_sector_context 降级判断
- fund_evidence: 字符串数组，引用 candidate_pool 中的 fund_quality_score、sector_fit_score、
  quality_reasons、return_3m_percent/return_6m_percent、max_drawdown_1y_percent、fund_scale_yi、tradeability
- validation_notes: 字符串数组，写清 quality_penalties、信息缺失、追高风险、新闻 stale/empty 等校验备注；无明显问题则 []
- points: 字符串数组，每条须引用 candidate_pool 内具体字段（如 nav_trend、return_3m_percent、
  estimated_daily_return_percent、sector_fund_flow）；daily_return_source=sector_estimate 时须写「估算」
- risks: 字符串数组，每只至少 1 条
- news_bullish: 字符串数组，仅引用 news_titles 或 topic_briefs.points.source_titles 中已有标题；无则 []
- suggested_amount_yuan: 始终输出 null。模型只判断候选与动作；服务端会忽略模型金额，并在最终守卫后按
  可用现金、预算、已有板块敞口、集中度、候选相关性、购买起点与日限额统一计算可核验首批金额
- 面向用户展示时必须使用中文标签，不要原样输出 fund_quality_score、sector_fit_score、quality_penalties、
  sector_opportunities、nav_trend、max_drawdown_1y_percent、estimated_daily_return_percent 等内部字段名；
  可写成“基金质量分”“板块匹配分”“系统校验提示”“系统筛出的主方向”“净值走势”“近1年最大回撤”“今日涨跌估算”等。

全局约束：
- 不得推荐 portfolio_gap.holdings_slim 中已持有的 fund_code
- 仅 quality_gate.status=eligible 的候选可用 action=分批买入；watch_only 只可建议关注/等待回调；
  excluded 不得进入 recommendations。没有 eligible 候选时须明确“本次暂无可执行买入建议”，不得凑满数量
- 不得承诺收益；不得编造 candidate_pool 外的代码或未提供的估值分位
- 仅 tradeability.freshness=fresh、purchase_state=open/limited、购买起点与日限额可核验的候选可提出分批买入；
  suspended/closed/unknown、双源冲突或限额未知时只能观察。服务端最终金额必须不低于购买起点且不超过日限额
- share_class_fee_status=standard_upper_bound_available 表示未折扣标准费率上限可复算，不代表用户销售平台最终费率；
  unverified 时须在 validation_notes 明确“真实申购/赎回费用待执行前核验”，不得宣称已选出最低成本份额
- 短周期持有必须结合 tradeability.standard_purchase_fee_tiers、redemption_fee_tiers 和销售服务费；
  费用不可核验、最短持有不足7天或标准费率上限成本过高时只能观察，不得用预期涨幅抵消费用门禁
- full_market 模式须先判断板块方向，再在方向内选基金；不得只按基金近1年收益排序
- 南向资金仅使用 stock_connect_flow，并只作港股资金面参考；板块主力使用 target_sector_context.sector_fund_flow
- sector_opportunities 是系统已用短期热度、主力资金与 mainline_regime 多周期相对强度生成的研究方向，
  推荐理由须优先引用它，而不是重新发明方向；mainline_regime 只改变研究排序，不得当作买入门禁或收益保证
- signal_backtest / candidate_factor_scores 按 confidence.level / factor_reliability 表述
- candidate_factor_scores.execution_qualified_fund_codes 只可作为量化加分证据；opportunity_first 下未覆盖不得单独否决买入，risk_first 下仍作为买入白名单；任何模式都不得把描述性覆盖写成量化背书
- profile.account_loss_review_percent 只用于账户/现有持仓亏损复核，不得直接与候选基金近1年最大回撤比较
- discovery_strategy=opportunity_first 时，持有目标按 20～60 个交易日理解；优先使用 nav_trend 的 20/60 日收益与最大回撤，一年回撤只影响风险提示与服务端仓位
- peer_research 只允许同组逐维比较；仅 applicable=true 且 available=true 的指标可解释，不适用与缺失不得补值；execution_tilt_eligible=false 时不得把分位用于执行提额
- benchmark_research.comparison_role=tracking_reference 时只能称“跟踪参考”，不得称正式超额
- benchmark_metrics 只有 status=qualified 才可引用；正式超额须同时满足 formal_excess_eligible=true，
  tracking_reference 的差值只能称“相对跟踪参考差异”；所有基准指标仅作描述，不得用于金额倾斜
- summary 或 caveats 须体现 news.freshness_label 对置信度的影响
- data_evidence 是字段级时点证据；stale/unavailable/none 不得支撑买入动作，is_estimate=true 必须降置信度
- discovery_facts.portfolio_position_truth 是持仓份额、成本和现金的唯一真值摘要；unknown/null 不得按 0 猜测；
  position_complete=false、ledger_truncated=true 或存在 pending/conflict 时，suggested_amount_yuan 必须为 null，
  不得生成任何可执行买入金额
- 新闻由系统预取并已做时效筛选；不得引用 news_titles/topic_briefs 之外的新闻，
  news.freshness_label 为 stale/empty/aging 时，新闻只能作背景，不能作为买入或追涨主依据
"""

_COMMON_REQUIREMENTS = [
    "仅从 discovery_facts.candidate_pool 选 0~3 只，不得推荐 holdings_slim 中已有 fund_code；无合格候选时允许空数组",
    "quality_gate=eligible 才可分批买入；watch_only 只能观察/等待，excluded 禁止推荐；不得为凑数降门槛",
    "每只 recommendations 须含 hold_horizon、risks（至少 1 条）、points（引用 candidate_pool 具体字段）",
    "每只 recommendations 须含 decision_path、sector_evidence、fund_evidence、validation_notes",
    "先判断板块方向，再比较方向内基金质量分，最后决定动作",
    "展示文本使用中文标签，不要原样输出 fund_quality_score/sector_fit_score/quality_penalties 等内部字段名",
    "estimated_daily_return_percent 且 daily_return_source=sector_estimate 时，points 须注明「估算」",
    "判断追高风险须参考 nav_trend.distance_from_high_percent / trend_label，不得只看 sector_heat",
    "news_bullish 仅引用 news_titles 或 topic_briefs.points.source_titles；无匹配则 []",
    "新闻仅使用系统预取的 news_titles/topic_briefs；过旧或为空的新闻不能作为买入主依据",
    "suggested_amount_yuan 始终为 null；最终金额由服务端确定性 allocator 统一计算，模型不得分配金额",
    "引用数字须来自 discovery_facts，禁止编造",
    "量化执行资格只可增加置信度；opportunity_first 下未覆盖本身不否决买入，risk_first 下仍要求量化执行资格；描述性覆盖永远不等于量化背书",
    "须按 data_evidence 校验数据时点、置信度与是否估算；过期或不可用字段不得支撑动作",
    "portfolio_position_truth 中 unknown/null 不得按 0；position_complete=false、ledger_truncated=true 或存在 pending/conflict 时 suggested_amount_yuan 必须为空",
    "share_class_fee_status=unverified 时须提示真实申购/赎回费用待核验，不得宣称份额成本最优",
    "tradeability 必须 fresh 且可申购；金额不得低于购买起点或突破单日限额，未知/冲突一律只观察",
    "标准费率仅是未折扣上限；短周期须通过持有期赎回费与销售服务费门禁，不得用收益预期绕过",
    "peer_research 仅作同组逐维研究；只解释 applicable=true 且 available=true 的指标，不适用与缺失不得补值；execution_tilt_eligible=false 时不得作为执行倾斜",
    "benchmark_research 仅 formal_excess_eligible=true 可称正式超额；tracking_reference 只能称跟踪参考",
    "benchmark_metrics 仅 status=qualified 可引用，且只作描述；基准身份本身不能证明跑赢，任何指标不得用于金额倾斜",
]

_FULL_MARKET_REQUIREMENTS = [
    *_COMMON_REQUIREMENTS,
    "基于 sector_heat 与 target_sector_context 做全市场横向对比",
    "先判断板块方向（sector_opportunities/target_sector_context），再比较方向内基金质量分，最后决定动作",
    "sector_opportunities 是系统结合短期热度、资金流与 mainline_regime 多周期证据生成的研究方向；mainline_regime 只改变研究排序，不得替代交易门禁",
    "portfolio_gap / holdings_slim 仅作背景，不要以「持仓缺口」为主叙事",
    "market_view 须覆盖热度靠前板块与相对冷门但有机会的方向",
    "引用南向须用 stock_connect_flow 且仅作港股资金面参考；板块主力须用 target_sector_context.sector_fund_flow",
]

_GAP_REQUIREMENTS = [
    *_COMMON_REQUIREMENTS,
    "结合 portfolio_gap（含 holdings_slim 的 sector_name、weight_percent）解释缺口补全理由",
    "优先推荐 holdings_slim 中未重仓、sector_heat 靠前的板块候选",
    "同 sector_name 合计权重不得超过 concentration_limit_percent，须在 amount_note 说明",
]

def _requirements_for_scan_mode(scan_mode: str) -> list[str]:
    normalized = scan_mode if scan_mode != "gap" else "portfolio_gap"
    if normalized == "full_market":
        return _FULL_MARKET_REQUIREMENTS
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
    portfolio_gap = discovery_facts.get("portfolio_gap") or {}
    target_sectors = list(portfolio_gap.get("target_sectors") or [])
    slim_pool = slim_candidate_pool_for_llm(
        pool,
        sector_heat=sector_heat_full,
        trade_date=trade_date,
    )
    trimmed_heat = trim_sector_heat_for_llm(
        sector_heat_full,
        target_sectors=target_sectors,
        focus_sectors=focus_sectors,
    )
    resolved_fund_type = fund_type_preference or discovery_facts.get("fund_type_preference") or "any"
    requirements = _requirements_for_scan_mode(scan_mode)
    discovery_strategy = str(
        discovery_facts.get("discovery_strategy")
        or (discovery_facts.get("effective_configuration") or {}).get("discovery_strategy")
        or "opportunity_first"
    )
    briefs = topic_briefs or []
    news = market_news or []
    minimal_briefs = analysis_mode == "fast"
    return {
        "today": str(
            session.get("calendar_date")
            or normalize_news_now().date().isoformat()
        ),
        "focus_sectors": focus_sectors,
        "scan_mode": scan_mode,
        "discovery_strategy": discovery_strategy,
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
            "stock_connect_flow": discovery_facts.get("stock_connect_flow"),
            "signal_backtest": discovery_facts.get("signal_backtest"),
            "sector_opportunities": _slim_sector_opportunities(
                discovery_facts.get("sector_opportunities") or []
            ),
            "news": discovery_facts.get("news"),
            "fund_announcements": compact_announcement_fetch_status(
                discovery_facts.get("fund_announcements") or {}
            ),
            "candidate_factor_scores": discovery_facts.get("candidate_factor_scores"),
            "candidate_peer_summary": discovery_facts.get("candidate_peer_summary"),
            "benchmark_contract": discovery_facts.get("benchmark_contract"),
            "benchmark_research_contract": discovery_facts.get(
                "benchmark_research_contract"
            ),
            "selection_strategy": discovery_facts.get("selection_strategy"),
            "discovery_strategy": discovery_strategy,
            "discovery_strategy_contract": discovery_facts.get(
                "discovery_strategy_contract"
            ),
            "portfolio_snapshot": compact_portfolio_snapshot_for_llm(
                discovery_facts.get("portfolio_snapshot")
                if isinstance(discovery_facts.get("portfolio_snapshot"), dict)
                else None
            ),
            "portfolio_position_truth": compact_portfolio_position_truth_for_llm(
                discovery_facts.get("portfolio_position_truth")
                if isinstance(discovery_facts.get("portfolio_position_truth"), dict)
                else None
            ),
            "data_evidence": compact_data_evidence_for_llm(
                discovery_facts.get("data_evidence")
                if isinstance(discovery_facts.get("data_evidence"), dict)
                else None
            ),
            "candidate_pool": slim_pool,
        },
        "requirements": requirements,
    }


def append_output_requirements_to_system(system_prompt: str) -> str:
    return (
        system_prompt.rstrip()
        + "\n\n"
        + OUTPUT_DISCOVERY_REQUIREMENTS.strip()
    )


def _slim_sector_opportunities(items: list[dict]) -> list[dict]:
    slimmed: list[dict] = []
    for item in items[:8]:
        row = {
            "sector_label": item.get("sector_label"),
            "track": item.get("track"),
            "score": item.get("score"),
            "research_score": item.get("research_score"),
            "confidence": item.get("confidence"),
            "entry_hint": item.get("entry_hint"),
            "evidence": item.get("evidence") or [],
            "penalties": item.get("penalties") or [],
            "change_1d_percent": item.get("change_1d_percent"),
            "change_5d_percent": item.get("change_5d_percent"),
            "today_main_force_net_yi": item.get("today_main_force_net_yi"),
            "cumulative_5d_net_yi": item.get("cumulative_5d_net_yi"),
            "pattern_label": item.get("pattern_label"),
            "mainline_regime": _slim_mainline_regime(item.get("mainline_regime")),
        }
        slimmed.append(row)
    return slimmed


def _slim_mainline_regime(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    features = value.get("features") if isinstance(value.get("features"), dict) else {}
    return {
        "schema_version": value.get("schema_version"),
        "status": value.get("status"),
        "score": value.get("score"),
        "confidence": value.get("confidence"),
        "feature_coverage": value.get("feature_coverage"),
        "research_ranking_only": True,
        "features": {
            "relative_return_20d_percent": features.get("relative_return_20d_percent"),
            "relative_strength_percentile": features.get("relative_strength_percentile"),
            "cumulative_20d_net_yi": features.get("cumulative_20d_net_yi"),
            "advancing_ratio_percent": features.get("advancing_ratio_percent"),
            "distance_from_ma20_percent": features.get("distance_from_ma20_percent"),
        },
        "evidence": list(value.get("evidence") or [])[:4],
        "risks": list(value.get("risks") or [])[:4],
    }
