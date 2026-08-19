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
只输出一个 JSON 对象（不要 Markdown 代码块）：
- title, summary（2-4句）, market_view, caveats（须含风险提示）
- recommendations: 0~4 项，每板块最多 1 项；每项含 fund_code, fund_name, sector_name, action,
  suggested_amount_yuan, amount_note, hold_horizon, confidence, decision_path,
  sector_evidence, fund_evidence, validation_notes, points, risks, news_bullish

字段约束：
- fund_code/fund_name 必须与 candidate_pool 一致；sector_name 用该基金 sector_label
- 不得推荐 holdings_slim 已持有代码，不得恢复 recommendation_candidate_scope 未列出的基金
- action 仅用：建议关注、分批买入、等待回调；confidence 仅用：高、中、低
- suggested_amount_yuan 始终为 null；hold_horizon 如 1-3个月
- decision_path：一句话写清「先方向 → 再比较基金 → 再动作」
- sector_evidence 引用 sector_opportunities 的 entry_state/资金/pattern；没有则说明降级到 sector_heat
- fund_evidence 引用质量分、板块身份、近3/6月收益、规模、nav_trend 或 entry_signal
- points 必须引用 candidate_pool 具体数字；sector_estimate 写成「（板块估算，截至 HH:MM）」
- risks 至少 1 条；买入须写出 entry_signal.invalidation_signals 对应退出条件
- news_bullish 只能引用 news_titles / topic_briefs 已有标题
- 展示用中文标签，不要输出内部字段名
- 新闻 stale/empty 时只能作背景；data_evidence 过期或估算不得支撑买入
"""

_COMPACT_FACTS_INSTRUCTION = (
    "只读事实。只可从 candidate_pool 白名单选基金；金额必须为 null。"
)


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
            "session": _slim_session_for_llm(discovery_facts.get("session")),
            "portfolio_gap": portfolio_gap,
            "fund_type_preference": resolved_fund_type,
            "sector_heat": trimmed_heat,
            "target_sector_context": _slim_target_sector_context(
                discovery_facts.get("target_sector_context") or [],
                priority_sector_labels=priority_sector_labels,
            ),
            "sector_opportunities": _slim_sector_opportunities(
                discovery_facts.get("sector_opportunities") or [],
                priority_sector_labels=priority_sector_labels,
            ),
            "recommendation_candidate_scope": _compact_recommendation_scope_for_llm(
                recommendation_scope,
                recommendation_codes,
            ),
            "fund_announcements": compact_announcement_fetch_status(
                discovery_facts.get("fund_announcements") or {}
            ),
            "candidate_factor_scores": _compact_candidate_factor_scores_for_llm(
                discovery_facts.get("candidate_factor_scores"),
                recommendation_codes,
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
            "selection_priority_score": item.get("selection_priority_score"),
            "overheat_flags": item.get("overheat_flags") or [],
            "first_tranche_scale": item.get("first_tranche_scale"),
            "trend_formation_probability": item.get(
                "trend_formation_probability"
            ),
            "formation_probability_band": item.get(
                "formation_probability_band"
            ),
            "probability_early_probe_eligible": item.get(
                "probability_early_probe_eligible"
            ),
            "flow_signal_state": item.get("flow_signal_state"),
            "flow_improving_probe_eligible": item.get(
                "flow_improving_probe_eligible"
            ),
            "waiting_reason_code": item.get("waiting_reason_code"),
            "entry_state": item.get("entry_state"),
            "entry_reason": item.get("entry_reason"),
            "invalidation_signals": list(
                item.get("invalidation_signals") or []
            )[:2],
            "opportunity_available": item.get("opportunity_available"),
            "execution_eligible": item.get("execution_eligible"),
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
        "max_recommendations",
        "ordered_eligible_fund_codes",
        "maximum_recommendations_per_sector",
        "actionable_sector_labels",
        "eligible_sector_labels",
        "unmatched_actionable_sector_labels",
        "research_sector_labels",
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


def _slim_session_for_llm(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "calendar_date",
        "effective_trade_date",
        "is_trading_day",
        "session_kind",
        "market_phase",
        "decision_window",
    )
    slim = {key: value.get(key) for key in keys if key in value}
    return slim or None
