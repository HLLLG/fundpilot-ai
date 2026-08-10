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
from app.services.discovery_recommendation_scope import (
    candidates_in_recommendation_scope,
    ensure_recommendation_candidate_scope,
)

OUTPUT_DISCOVERY_REQUIREMENTS = """
你必须只输出一个 JSON 对象（不要 Markdown 代码块），字段：
- title: 报告标题
- summary: 2-4 句市场与配置总结
- market_view: 对大盘/板块的简短看法
- recommendations: 数组，0~4 项；每个板块最多 1 项；没有合格候选时允许为空；每项含 fund_code, fund_name, sector_name, action,
  suggested_amount_yuan, amount_note, hold_horizon, confidence, decision_path,
  sector_evidence, fund_evidence, validation_notes, points, risks, news_bullish
- caveats: 字符串数组，须含风险提示

recommendations 字段约束：
- fund_code / fund_name 必须与 discovery_facts.candidate_pool 对应条目完全一致
- discovery_facts.candidate_pool 已是服务端按方向动作边界筛出的推荐白名单；不得从等待/研究方向补位，
  也不得自行恢复 recommendation_candidate_scope 未列出的基金
- sector_name 须与 candidate_pool 中该基金的 sector_label 一致
- action 仅用：建议关注、分批买入、等待回调
- confidence 仅用：高、中、低
- hold_horizon 示例：2-4周、1-3个月、3-6个月
- decision_path: 1 句话，必须按「先判断板块方向 → 再比较方向内候选基金质量 → 最后决定动作」说明
- sector_evidence: 字符串数组，引用 sector_opportunities 中的 score、track、confidence、资金流、pattern；
  若没有对应 sector_opportunities，须说明使用 sector_heat / target_sector_context 降级判断
- fund_evidence: 字符串数组，引用 candidate_pool 中的 fund_quality_score、sector_fit_score、
  sector_identity_status、sector_identity_eligible、
  quality_reasons、return_3m_percent/return_6m_percent、max_drawdown_1y_percent、fund_scale_yi
- validation_notes: 字符串数组，写清 quality_penalties、信息缺失、新闻 stale/empty 等校验备注；仅结构化 overheat_flags 非空时可写追高/短期加速风险；无明显问题则 []
- points: 字符串数组，每条须引用 candidate_pool 内具体字段（如 nav_trend、return_3m_percent、
  estimated_daily_return_percent、sector_fund_flow）；daily_return_source=sector_estimate 时须写「估算」
- risks: 字符串数组，每只至少 1 条
- news_bullish: 字符串数组，仅引用 news_titles 或 topic_briefs.points.source_titles 中已有标题；无则 []
- suggested_amount_yuan: 始终输出 null。模型只判断候选与动作；服务端会忽略模型金额，并在最终守卫后按
  本次可投入预算、已有板块敞口、集中度与候选风险相关性统一计算本次参考金额
- 面向用户展示时必须使用中文标签，不要原样输出 fund_quality_score、sector_fit_score、quality_penalties、
  sector_opportunities、nav_trend、max_drawdown_1y_percent、estimated_daily_return_percent 等内部字段名；
  可写成“基金质量分”“板块关联排序分”“板块身份状态”“系统校验提示”“系统筛出的主方向”“净值走势”“近1年最大回撤”“今日涨跌估算”等。

全局约束：
- 不得推荐 portfolio_gap.holdings_slim 中已持有的 fund_code
- 仅 quality_gate.status=eligible 的候选可用 action=分批买入；watch_only 只可建议关注/等待回调；
  excluded 不得进入 recommendations。没有候选同时通过方向、基金质量、载体质量与板块身份门槛时，
  须明确“本次暂无买入建议”并按 recommendation_candidate_scope.candidate_decisions 说明等待/观察原因，不得凑满数量
- 不得承诺收益；不得编造 candidate_pool 外的代码或未提供的估值分位
- 本功能不获取或判断具体销售平台的申购状态、起购额、限额和交易费率；不得臆造这些信息，也不得因其缺失把候选降为观察
- full_market 模式须先判断板块方向，再在方向内选基金；不得只按基金近1年收益排序
- 南向资金仅使用 stock_connect_flow，并只作港股资金面参考；板块主力使用 target_sector_context.sector_fund_flow
- sector_opportunities 含 score_policy_version（sector_entry_maturity.2026-07.v2 或 2026-08.v3）时，须以 entry_state 与提前试仓字段共同判断方向动作：
  ready_to_start 表示趋势、资金参与度与价格位置已同时通过，可在基金质量、数据与组合约束通过时使用分批买入；
  ready_on_pullback 通常等待；但 V3 若趋势与参与度已通过、唯一失败项是板块价格位置，且
  fund_entry_signal.entry_ready=true，可用基金自身20日修复替代价格位置项；或
  flow_improving_probe_eligible=true 且基金自身入场信号通过时缩小本次参考金额；或 forming 方向的
  probability_early_probe_eligible=true 且基金 fund_entry_signal.entry_ready/early_probe_ready=true 时，
  按 trend_formation_probability 对应的 first_tranche_scale 提前试仓；其余 forming 只能建议关注
- V3 的 waiting_reason_code 用于解释等待：flow_confirmation=等待资金确认，fund_entry_confirmation=等待基金自身信号，
  probability_fund_confirmation=趋势成形信号分已达试仓线但仍等待基金早期信号，
  structure_repair=等待结构修复；不得把所有等待都描述成价格需要回调
- v3 的 overheat_flags 是风险披露而非否决理由：命中时按 first_tranche_scale 缩小本次参考金额，
  文案须说明"短期加速、本次金额更小；买入后的加减仓由日报重新分析"，不得因此改写为不可买入
- v3 没有"入场成熟度"这个分数；三个分块（趋势强度/资金参与度/价格位置）各自独立，
  权重见 block_weights，不得把它们描述为三重确认
- mainline_regime 单独仍只参与研究排序；只有方向成熟度 V2/V3 的完整组合状态，或 V3 提前试仓与基金早期信号共同通过，才可生成本次参考金额，不构成收益保证
- signal_backtest / candidate_factor_scores 按 confidence.level / factor_reliability 表述
- candidate_factor_scores.execution_qualified_fund_codes 只可作为量化加分证据；opportunity_first 下未覆盖不得单独否决买入，risk_first 下仍作为买入白名单；任何模式都不得把描述性覆盖写成量化背书
- profile.account_loss_review_percent 只用于账户/现有持仓亏损复核，不得直接与候选基金近1年最大回撤比较
- discovery_strategy=opportunity_first 时，持有目标按 20～60 个交易日理解；质量门内优先未封顶的
  20/60 日收益、年化波动与回撤修复；一年回撤不参与机会排序，只影响风险提示与服务端仓位
- 高弹性买入候选的 risks 必须写出 fund_entry_signal.invalidation_signals 对应的退出复核条件；
  不得把止损描述成保证按指定价格成交
- 只有 sector_opportunities.overheat_flags 或 fund_entry_signal.overheat_flags 非空时可写追高/短期加速风险；
  单独接近20日高点不是追高证据
- peer_research 只允许同组逐维比较；仅 applicable=true 且 available=true 的指标可解释，不适用与缺失不得补值；execution_tilt_eligible=false 时不得把分位用于执行提额
- benchmark_research.comparison_role=tracking_reference 时只能称“跟踪参考”，不得称正式超额
- benchmark_metrics 只有 status=qualified 才可引用；正式超额须同时满足 formal_excess_eligible=true，
  tracking_reference 的差值只能称“相对跟踪参考差异”；所有基准指标仅作描述，不得用于金额倾斜
- summary 或 caveats 须体现 news.freshness_label 对置信度的影响
- data_evidence 是字段级时点证据；stale/unavailable/none 不得支撑买入动作，is_estimate=true 必须降置信度
- discovery_facts.portfolio_position_truth 是持仓份额和成本的唯一真值摘要；unknown/null 不得按 0 猜测；
  模型的 suggested_amount_yuan 始终为 null；份额未确认不阻断方向判断，服务端可使用 holdings_slim 的估算市值、
  用户明确填写的本次可投入预算与集中度规则计算本次金额；账户现金字段不参与本次扫描金额；
  买入并录入持仓后，后续加减仓由持仓日报基于最新数据重新分析
- 新闻由系统预取并已做时效筛选；不得引用 news_titles/topic_briefs 之外的新闻，
  news.freshness_label 为 stale/empty/aging 时，新闻只能作背景，不能作为买入或追涨主依据
"""

_COMMON_REQUIREMENTS = [
    "仅从 discovery_facts.candidate_pool 推荐白名单选 0~4 只，每个板块最多 1 只（同方向只取综合质量最优的那一只）；不得推荐 holdings_slim 中已有 fund_code；无合格候选时允许空数组",
    "等待/研究方向不得占用推荐名额；不得跨方向凑数，也不得恢复 recommendation_candidate_scope 未列出的基金",
    "quality_gate=eligible 才可分批买入；watch_only 只能观察/等待，excluded 禁止推荐；不得为凑数降门槛",
    "每只 recommendations 须含 hold_horizon、risks（至少 1 条）、points（引用 candidate_pool 具体字段）",
    "每只 recommendations 须含 decision_path、sector_evidence、fund_evidence、validation_notes",
    "先判断板块方向；基金质量只作硬准入，门内按机会分、波动弹性与修复信号排序，最后决定动作",
    "方向成熟度 V2/V3 存在时按 entry_state；V3 ready_on_pullback 可在基金修复替代结构项，或资金同日改善且基金信号通过时缩小本次金额；forming 仅在 probability_early_probe_eligible=true 且基金早期信号通过时提前试仓；V3 过热仅缩小本次金额",
    "展示文本使用中文标签，不要原样输出 fund_quality_score/sector_fit_score/quality_penalties 等内部字段名",
    "sector_fit_score 仅是关联排序分，不得替代 sector_identity_status=verified 与 sector_identity_eligible=true 的代码级身份门槛",
    "estimated_daily_return_percent 且 daily_return_source=sector_estimate 时，points 须注明「估算」",
    "判断入场位置须参考 fund_entry_signal 与20日修复率、离低点反弹、近5日方向，不得只看 sector_heat 或距高点",
    "板块 selection_priority_score 仅用于同一入场状态内排序；资金拐点优先于普通等待，高弹性只加排序分，不替代趋势、资金、结构和数据门槛",
    "仅结构化 overheat_flags 非空时可写追高/短期加速风险；接近20日高点本身不是否决理由",
    "买入候选必须给出可核验的修复失效/退出条件，不得暗示止损成交价有保证",
    "news_bullish 仅引用 news_titles 或 topic_briefs.points.source_titles；无匹配则 []",
    "新闻仅使用系统预取的 news_titles/topic_briefs；过旧或为空的新闻不能作为买入主依据",
    "suggested_amount_yuan 始终为 null；最终金额由服务端确定性 allocator 统一计算，模型不得分配金额",
    "引用数字须来自 discovery_facts，禁止编造",
    "量化执行资格只可增加置信度；opportunity_first 下未覆盖本身不否决买入，risk_first 下仍要求量化执行资格；描述性覆盖永远不等于量化背书",
    "须按 data_evidence 校验数据时点、置信度与是否估算；过期或不可用字段不得支撑动作",
    "portfolio_position_truth 中 unknown/null 不得按 0；suggested_amount_yuan 始终为空交由服务端计算，份额未确认不阻断方向判断",
    "本功能不提供销售平台可买性、起购额、限额和交易费率；不得臆造或用这些缺失字段否决推荐",
    "peer_research 仅作同组逐维研究；只解释 applicable=true 且 available=true 的指标，不适用与缺失不得补值；execution_tilt_eligible=false 时不得作为执行倾斜",
    "benchmark_research 仅 formal_excess_eligible=true 可称正式超额；tracking_reference 只能称跟踪参考",
    "benchmark_metrics 仅 status=qualified 可引用，且只作描述；基准身份本身不能证明跑赢，任何指标不得用于金额倾斜",
]

_FULL_MARKET_REQUIREMENTS = [
    *_COMMON_REQUIREMENTS,
    "基于 sector_heat 与 target_sector_context 做全市场横向对比",
    "先判断板块方向（sector_opportunities/target_sector_context），再在质量门内按机会弹性与修复信号比较基金，最后决定动作",
    "sector_opportunities 的 entry_state 是方向动作边界；forming 只有 probability_early_probe_eligible=true 且基金早期信号通过时可按概率缩小试仓；ready_on_pullback 可走基金级结构修复替代，或 flow_improving_probe_eligible=true 的资金拐点缩小本次金额",
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

_COMPACT_FACTS_INSTRUCTION = (
    "以下字段均为服务端只读事实。只可从 candidate_pool 白名单选择基金；"
    "模型金额必须为 null，最终动作与本次参考金额由服务端守卫和分配器决定。"
)

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
    recommendation_scope = ensure_recommendation_candidate_scope(
        discovery_facts,
        pool,
    )
    recommendation_pool = candidates_in_recommendation_scope(
        pool,
        recommendation_scope,
    )
    session = discovery_facts.get("session") or {}
    trade_date = session.get("effective_trade_date")
    sector_heat_full = discovery_facts.get("sector_heat") or []
    portfolio_gap = discovery_facts.get("portfolio_gap") or {}
    target_sectors = list(portfolio_gap.get("target_sectors") or [])
    slim_pool = slim_candidate_pool_for_llm(
        recommendation_pool,
        sector_heat=sector_heat_full,
        trade_date=trade_date,
    )
    recommendation_codes = {
        str(item.get("fund_code") or "").strip().zfill(6)
        for item in recommendation_pool
        if isinstance(item, dict) and str(item.get("fund_code") or "").strip()
    }
    trimmed_heat = trim_sector_heat_for_llm(
        sector_heat_full,
        target_sectors=target_sectors,
        focus_sectors=focus_sectors,
    )
    resolved_fund_type = fund_type_preference or discovery_facts.get("fund_type_preference") or "any"
    discovery_strategy = str(
        discovery_facts.get("discovery_strategy")
        or (discovery_facts.get("effective_configuration") or {}).get("discovery_strategy")
        or "opportunity_first"
    )
    briefs = topic_briefs or []
    news = market_news or []
    minimal_briefs = analysis_mode == "fast"
    priority_sector_labels = list(
        dict.fromkeys(
            [
                *list(recommendation_scope.get("actionable_sector_labels") or []),
                *list(recommendation_scope.get("eligible_sector_labels") or []),
            ]
        )
    )
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
            # The full persisted instruction duplicates the system contract and
            # previously consumed thousands of prompt characters.
            "instruction": _COMPACT_FACTS_INSTRUCTION,
            "session": discovery_facts.get("session"),
            "portfolio_gap": portfolio_gap,
            "fund_type_preference": resolved_fund_type,
            "sector_heat": trimmed_heat,
            "target_sector_context": _slim_target_sector_context(
                discovery_facts.get("target_sector_context") or [],
                priority_sector_labels=priority_sector_labels,
            ),
            "stock_connect_flow": discovery_facts.get("stock_connect_flow"),
            "signal_backtest": discovery_facts.get("signal_backtest"),
            "sector_opportunities": _slim_sector_opportunities(
                discovery_facts.get("sector_opportunities") or [],
                priority_sector_labels=priority_sector_labels,
            ),
            "recommendation_candidate_scope": _compact_recommendation_scope_for_llm(
                recommendation_scope,
                recommendation_codes,
            ),
            "news": discovery_facts.get("news"),
            "fund_announcements": compact_announcement_fetch_status(
                discovery_facts.get("fund_announcements") or {}
            ),
            "candidate_factor_scores": _compact_candidate_factor_scores_for_llm(
                discovery_facts.get("candidate_factor_scores"),
                recommendation_codes,
            ),
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
                else None,
                fund_codes=recommendation_codes,
            ),
            "candidate_pool": slim_pool,
        },
    }


def append_output_requirements_to_system(system_prompt: str) -> str:
    return (
        system_prompt.rstrip()
        + "\n\n"
        + OUTPUT_DISCOVERY_REQUIREMENTS.strip()
    )


def _slim_sector_opportunities(
    items: list[dict],
    *,
    priority_sector_labels: list[str] | None = None,
    limit: int = 5,
) -> list[dict]:
    slimmed: list[dict] = []
    for item in _prioritize_sector_rows(
        items,
        priority_sector_labels=priority_sector_labels,
        limit=limit,
    ):
        row = {
            "sector_label": item.get("sector_label"),
            "track": item.get("track"),
            "score": item.get("score"),
            "selection_priority_score": item.get("selection_priority_score"),
            "selection_path": item.get("selection_path"),
            "score_policy_version": item.get("score_policy_version"),
            "direction_score": item.get("direction_score"),
            # v3 的三个正交分块；v2 报告里为 None，反之亦然。
            "trend_strength_score": item.get("trend_strength_score"),
            "participation_score": item.get("participation_score"),
            "position_risk_score": item.get("position_risk_score"),
            "block_weights": item.get("block_weights"),
            "overheat_flags": item.get("overheat_flags") or [],
            "first_tranche_scale": item.get("first_tranche_scale"),
            "trend_formation_probability": item.get(
                "trend_formation_probability"
            ),
            "formation_probability_band": item.get(
                "formation_probability_band"
            ),
            "probability_tranche_scale": item.get(
                "probability_tranche_scale"
            ),
            "probability_early_probe_eligible": item.get(
                "probability_early_probe_eligible"
            ),
            "flow_signal_state": item.get("flow_signal_state"),
            "flow_improving_probe_eligible": item.get(
                "flow_improving_probe_eligible"
            ),
            "waiting_reason_code": item.get("waiting_reason_code"),
            "data_coverage": item.get("data_coverage"),
            "evidence_quality": item.get("evidence_quality"),
            "entry_state": item.get("entry_state"),
            "entry_reason": item.get("entry_reason"),
            "entry_triggers": list(item.get("entry_triggers") or [])[:2],
            "invalidation_signals": list(
                item.get("invalidation_signals") or []
            )[:2],
            "opportunity_available": item.get("opportunity_available"),
            "execution_eligible": item.get("execution_eligible"),
            "confidence": item.get("confidence"),
            "entry_hint": item.get("entry_hint"),
            "evidence": list(item.get("evidence") or [])[:2],
            "penalties": list(item.get("penalties") or [])[:2],
            "change_1d_percent": item.get("change_1d_percent"),
            "change_5d_percent": item.get("change_5d_percent"),
            "today_main_force_net_yi": item.get("today_main_force_net_yi"),
            "cumulative_5d_net_yi": item.get("cumulative_5d_net_yi"),
            "pattern_label": item.get("pattern_label"),
        }
        slimmed.append(row)
    return slimmed


def _slim_target_sector_context(
    items: list[dict],
    *,
    priority_sector_labels: list[str] | None = None,
    limit: int = 5,
) -> list[dict]:
    return [
        {
            key: item.get(key)
            for key in (
                "sector_label",
                "heat_score",
                "change_1d_percent",
                "change_5d_percent",
                "sector_fund_flow",
            )
            if key in item
        }
        for item in _prioritize_sector_rows(
            items,
            priority_sector_labels=priority_sector_labels,
            limit=limit,
        )
    ]


def _prioritize_sector_rows(
    items: list[dict],
    *,
    priority_sector_labels: list[str] | None,
    limit: int,
) -> list[dict]:
    rows = [item for item in items if isinstance(item, dict)]
    by_label = {
        str(item.get("sector_label") or "").strip(): item
        for item in rows
        if str(item.get("sector_label") or "").strip()
    }
    selected: list[dict] = []
    seen: set[str] = set()
    for raw_label in priority_sector_labels or []:
        label = str(raw_label or "").strip()
        item = by_label.get(label)
        if item is not None and label not in seen:
            selected.append(item)
            seen.add(label)
    for item in rows:
        label = str(item.get("sector_label") or "").strip()
        if not label or label in seen:
            continue
        selected.append(item)
        seen.add(label)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _compact_recommendation_scope_for_llm(
    value: object,
    allowed_codes: set[str],
) -> dict:
    if not isinstance(value, dict):
        return {}
    scalar_and_list_keys = (
        "schema_version",
        "policy_enforced",
        "max_recommendations",
        "ordered_eligible_fund_codes",
        "maximum_recommendations_per_sector",
        "actionable_sector_labels",
        "eligible_sector_labels",
        "unmatched_actionable_sector_labels",
        "research_sector_labels",
        "instruction",
    )
    result = {key: value.get(key) for key in scalar_and_list_keys if key in value}
    result["sector_funnel"] = [
        {
            key: row.get(key)
            for key in (
                "sector_label",
                "entry_state",
                "direction_path",
                "recalled_count",
                "eligible_count",
                "conditional_wait_count",
                "watch_only_count",
                "rejected_reason_counts",
            )
            if key in row
        }
        for row in value.get("sector_funnel") or []
        if isinstance(row, dict)
    ]
    result["candidate_decisions"] = [
        {
            key: row.get(key)
            for key in (
                "fund_code",
                "fund_name",
                "sector_label",
                "status",
                "entry_path",
                "fund_gates_passed",
                "direction_gate_passed",
                "reason_codes",
            )
            if key in row
        }
        for row in value.get("candidate_decisions") or []
        if isinstance(row, dict)
        and str(row.get("fund_code") or "").strip().zfill(6) in allowed_codes
    ]
    return result


def _compact_candidate_factor_scores_for_llm(
    value: object,
    allowed_codes: set[str],
) -> dict | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: value.get(key)
        for key in (
            "available",
            "universe_size",
            "reliability_scope",
            "model_version",
            "selection_policy",
            "eligible_candidate_count",
            "execution_qualified_coverage_percent",
        )
        if key in value
    }
    ic_status = value.get("ic_status")
    if isinstance(ic_status, dict):
        result["ic_status"] = {
            key: ic_status.get(key)
            for key in (
                "state",
                "available",
                "stale",
                "confidence_eligible",
                "confidence_block_reasons",
                "run_date",
                "age_days",
            )
            if key in ic_status
        }
    for key in (
        "selected_fund_codes",
        "descriptive_applicable_fund_codes",
        "execution_qualified_fund_codes",
        "applicable_fund_codes",
    ):
        result[key] = [
            str(code).strip().zfill(6)
            for code in value.get(key) or []
            if str(code).strip().zfill(6) in allowed_codes
        ]
    result["holdings"] = [
        {
            key: row.get(key)
            for key in (
                "fund_code",
                "fund_name",
                "composite_grade",
                "composite_score",
                "factor_percentiles",
                "peer_group_label",
                "peer_count",
                "feature_completeness",
                "descriptive_applicable",
                "execution_qualified",
                "execution_qualification",
                "target_feature_as_of",
                "target_feature_freshness",
                "factor_reliability",
            )
            if key in row
        }
        for row in value.get("holdings") or []
        if isinstance(row, dict)
        and str(row.get("fund_code") or "").strip().zfill(6) in allowed_codes
    ]
    return result


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
