from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable, Mapping
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
from app.services.news_freshness import build_news_pipeline_context, normalize_news_now
from app.services.news_service import compact_announcement_fetch_status
from app.services.pipeline_concurrency import run_with_request_user
from app.services.portfolio_snapshot import (
    build_factor_scores_for_facts,
    build_portfolio_trend_context,
    build_risk_metrics_for_facts,
)
from app.services.trading_session import build_trading_session
from app.services.decision_data_evidence import attach_analysis_data_evidence
from app.services.benchmark_mapping_service import (
    BENCHMARK_MAPPING_SCHEMA_VERSION,
    load_decision_benchmark_specs,
)
from app.services.fund_benchmark_research import (
    BENCHMARK_RESEARCH_SCHEMA_VERSION,
    build_fund_benchmark_research_batch,
    summarize_benchmark_research,
)
from app.services.fund_tradeability import (
    build_tradeability_gate,
    compact_tradeability_for_llm,
    resolve_fund_tradeability_profiles,
)

AnalysisPayloadPhase = Literal[1, 2, 3]

FACTOR_SCORE_TIMEOUT_SECONDS = 4.0
RISK_METRICS_TIMEOUT_SECONDS = 3.0

# 迁入 system 的完整输出约束（不再每条请求在 user JSON 重复）
OUTPUT_REQUIREMENTS_SYSTEM = (
    "analysis_facts.portfolio_position_truth 是持仓份额、成本和现金的唯一真值摘要；"
    "unknown/null 不得按 0 猜测。日报统一采用相对当前估算持仓的百分比建议，amount_yuan 必须始终为 null，"
    "不得自行计算份额或固定金额；suggested_position_change_percent 由服务端确定性规则生成，模型须省略或输出 null。"
    "estimated_position_change_amount_yuan 同样由服务端按最终比例和报告生成时持仓估值折算，模型不得输出。"
    "position_complete=false、ledger_truncated=true 或存在 pending/conflict 不阻断百分比方向建议；"
    "只要持仓金额与市场方向证据新鲜可用，仍须从 allowed_actions 中给出加仓、减仓或观察。"
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
    "action 的唯一合法集合是 analysis_facts.allowed_actions；必须逐字从该数组选择，不得依赖固定数量或另造动作。"
    "若 analysis_facts.portfolio.suggested_action 为 risk_review 或 risk_level 为 high，禁止加仓类 action。"
    "analysis_facts.holdings[].transaction_execution 是交易执行硬门禁："
    "分批加仓仅在 add_status=eligible 时才可输出，具体比例由服务端按板块机会分分档，并结合追加起购额、单日限额与集中度收紧；"
    "减仓类动作即使 redemption_status=eligible，也不得猜测逐笔持有期、锁定期或赎回费；"
    "acquisition_lot_status=unverified 时仍可给减仓方向，但须提示实际赎回前核对持有期与费用。"
    "任何场景都不得给 amount_yuan 或份额数。"
    "recommendations 可省略或仅 1 条组合级说明，禁止长新闻摘要堆砌。"
    "判断当日涨跌优先 daily_return_percent，否则用 sector_return_percent 估算；"
    "判断累计持有收益/浮亏须用 estimated_holding_return_percent（与界面「持有」列一致），"
    "勿用 holding_return_percent（昨日结算）。"
    "区分 sector_return_percent（板块）、holding_return_percent（昨日结算）、"
    "estimated_holding_return_percent（累计持有）、daily_return_percent（当日）。"
    "基金代码 000000 须提示补全代码。不做实盘交易指令。"
    "analysis_facts.holdings[].nav_trend 为净值摘要，不得编造未给出的序列；"
    "sector_momentum/sector_intraday/sector_fund_flow 为短线提示；stock_connect_flow 仅提供南向数值，"
    "并且只作港股资金面的独立参考。"
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
    "analysis_facts.market_breadth 是大盘情绪温度计（自上而下）：signal_mode=closing 的"
    "sentiment_level 才是近2年创新高低百分位口径；signal_mode=intraday 则是当日上涨/下跌/"
    "平盘与赚钱效应准实时口径，closing_* 仅为上一完整交易日背景，不得混称历史百分位。"
    "decision_eligible=false 或 freshness_status=stale 时只能作背景，禁止支撑强动作。"
    "涨跌停家数等仅为当日快照，不得当作历史回测结论使用。"
    "analysis_facts.holdings[].flow_divergence_backtest 是该持仓板块「量价背离」信号"
    "（涨但资金流出/跌但资金流入）的历史回测：by_rule 各桶含 trigger_count、hit_rate_percent、"
    "edge_percent、significant；significant=true 时可作为该持仓板块方向判断的量化背书之一，"
    "与 sector_opportunity/evidence 合并判断，不得单独作为加仓或减仓的唯一依据。"
)

OUTPUT_REQUIREMENTS_USER = [
    "portfolio_position_truth 中 unknown/null 不得按 0；amount_yuan 始终为空，比例与估算调整金额均留空交由服务端计算；份额未确认不阻断百分比方向判断",
    "analysis_facts 为系统计算的只读事实，不得改写其中任何数字",
    "输出 title、summary、fund_recommendations、caveats；每只基金恰好 1 条 recommendation",
    "action 仅限 analysis_facts.allowed_actions；risk_review 或 high 禁止加仓类",
    "news_bullish/news_bearish 为字符串数组，须来自 news_titles 或 topic_briefs.points.source_titles",
    "每只基金 points 1-3 条：含权重/持有收益/净值或板块数据，且至少 1 条写下一交易日条件化预案",
    "引用 sector_intraday.pattern_label、nav_trend、sector_fund_gap_percent、sector_fund_flow 时须用 analysis_facts 中的数字",
    "每只基金须含 confidence、decision_path、sector_evidence、fund_evidence、validation_notes、risks（至少1条）",
    "decision_path 须体现「先判断板块方向(sector_opportunity)→再看基金自身证据→最后给出动作」的顺序",
]

BENCHMARK_OUTPUT_REQUIREMENTS_SYSTEM = (
    "analysis_facts.benchmark_specs is read-only point-in-time evidence loaded before generation. "
    "Only tier=fund_contract_exact with formal_excess_eligible=true is a formal performance "
    "benchmark. tracked_index_exact is reference-only; unavailable benchmark identity must "
    "never be guessed or upgraded. Benchmark identity alone never proves outperformance. "
    "Use numeric fund-versus-benchmark claims only from qualified analysis_facts.benchmark_research; "
    "formal_excess values require comparison_role=formal_excess, while tracking_reference values "
    "must be described only as reference/tracking differences."
)
OUTPUT_REQUIREMENTS_SYSTEM = (
    OUTPUT_REQUIREMENTS_SYSTEM + "\n" + BENCHMARK_OUTPUT_REQUIREMENTS_SYSTEM
)
OUTPUT_REQUIREMENTS_USER.append(
    "benchmark_specs 仅用于其声明的角色：正式合同基准可评估超额，"
    "跟踪指数仅作参照，unavailable 不得猜测；没有 qualified benchmark_research 时不得声称跑赢或跟踪良好"
)
_HOLDING_LLM_DROP_KEYS = frozenset(
    {
        "management_fee",
        "fund_scale_yi",
        "fund_scale_evidence",
        "fund_scale_source",
        "fund_scale_as_of",
        "fund_scale_freshness",
        "fund_scale_fetched_at",
        "fund_scale_basis",
        "management_fee_annual_recurring",
    }
)

_MANAGEMENT_FEE_SEMANTICS = (
    "基金管理的经常性年费率，已持续体现在基金净值中；不是本次申购费或赎回费，"
    "不得从收益、预算或建议金额中再次扣除。"
)

_PORTFOLIO_SNAPSHOT_LLM_KEYS = (
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
_DATA_EVIDENCE_ITEM_LLM_KEYS = (
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
_POSITION_TRUTH_LLM_KEYS = (
    "schema_version",
    "snapshot_id",
    "ledger_version",
    "position_as_of",
    "position_complete",
    "position_truth_status",
    "pending_transaction_count",
    "known_unsettled_transaction_count",
    "conflict_count",
    "ledger_truncated",
    "total_market_value_yuan",
    "instruction",
)
_POSITION_TRUTH_ROW_LLM_KEYS = (
    "fund_code",
    "fund_name",
    "settled_shares",
    "shares_quality",
    "market_value_yuan",
    "cost_basis_total_yuan",
    "cost_quality",
    "fee_complete",
)

_DAILY_DRAFT_SCALAR_LLM_KEYS = (
    "fund_code",
    "fund_name",
    "action",
    "amount_yuan",
    "amount_note",
    "confidence",
    "hold_horizon",
    "decision_path",
    "suggested_position_change_percent",
    "suggested_position_change_basis",
    "holding_index",
)
_DISCOVERY_DRAFT_SCALAR_LLM_KEYS = (
    "fund_code",
    "fund_name",
    "sector_name",
    "action",
    "suggested_amount_yuan",
    "amount_note",
    "confidence",
    "hold_horizon",
    "decision_path",
    "suggested_position_change_percent",
    "suggested_position_change_basis",
)
_DAILY_DRAFT_TEXT_LIST_LLM_KEYS = (
    "news_bullish",
    "news_bearish",
    "points",
    "risks",
    "sector_evidence",
    "fund_evidence",
)
_DISCOVERY_DRAFT_TEXT_LIST_LLM_KEYS = (
    "news_bullish",
    "points",
    "risks",
    "sector_evidence",
    "fund_evidence",
)


def _llm_scalar(value: Any) -> Any:
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _compact_draft_report_for_llm(
    value: Mapping[str, Any] | None,
    *,
    recommendation_key: str,
    top_level_keys: tuple[str, ...],
    scalar_keys: tuple[str, ...],
    text_list_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Allow-list an untrusted model draft before sending it to another model.

    ``validation_notes`` is deliberately omitted.  Older parsers stringify
    arbitrary nested values in that field, so even a scalar-only re-projection
    cannot prove that it is prose rather than a serialized ledger or audit.  The
    review model can recreate notes from the compact facts it receives.
    """

    if not isinstance(value, Mapping):
        return {recommendation_key: []}
    result = {
        key: _llm_scalar(value.get(key))
        for key in top_level_keys
        if key in value
    }
    result["caveats"] = [
        item for item in value.get("caveats") or [] if isinstance(item, str)
    ]
    recommendations: list[dict[str, Any]] = []
    for raw in value.get(recommendation_key) or []:
        if not isinstance(raw, Mapping):
            continue
        row = {
            key: _llm_scalar(raw.get(key))
            for key in scalar_keys
            if key in raw
        }
        for key in text_list_keys:
            if key not in raw:
                continue
            row[key] = [
                item for item in raw.get(key) or [] if isinstance(item, str)
            ]
        recommendations.append(row)
    result[recommendation_key] = recommendations
    return result


def compact_daily_draft_report_for_llm(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return _compact_draft_report_for_llm(
        value,
        recommendation_key="fund_recommendations",
        top_level_keys=("title", "summary"),
        scalar_keys=_DAILY_DRAFT_SCALAR_LLM_KEYS,
        text_list_keys=_DAILY_DRAFT_TEXT_LIST_LLM_KEYS,
    )


def compact_discovery_draft_report_for_llm(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return _compact_draft_report_for_llm(
        value,
        recommendation_key="recommendations",
        top_level_keys=("title", "summary", "market_view"),
        scalar_keys=_DISCOVERY_DRAFT_SCALAR_LLM_KEYS,
        text_list_keys=_DISCOVERY_DRAFT_TEXT_LIST_LLM_KEYS,
    )


def compact_portfolio_snapshot_for_llm(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project portfolio provenance without exposing ledger/position internals."""

    if not isinstance(value, Mapping):
        return None
    return {
        key: _llm_scalar(value.get(key))
        for key in _PORTFOLIO_SNAPSHOT_LLM_KEYS
    }


def compact_data_evidence_for_llm(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project the evidence registry through a scalar-only field allow-list."""

    if not isinstance(value, Mapping):
        return None
    return {
        "schema_version": _llm_scalar(value.get("schema_version")),
        "decision_ready": _llm_scalar(value.get("decision_ready")),
        "blocking_reasons": [
            reason
            for reason in value.get("blocking_reasons") or []
            if isinstance(reason, str)
        ],
        "items": [
            {
                key: _llm_scalar(item.get(key))
                for key in _DATA_EVIDENCE_ITEM_LLM_KEYS
            }
            for item in value.get("items") or []
            if isinstance(item, Mapping)
        ],
    }


def compact_portfolio_position_truth_for_llm(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Re-project compact position truth so injected ledger fields cannot hitchhike."""

    if not isinstance(value, Mapping):
        return None
    result = {
        key: _llm_scalar(value.get(key))
        for key in _POSITION_TRUTH_LLM_KEYS
        if key in value
    }
    cash = value.get("cash")
    if isinstance(cash, Mapping):
        result["cash"] = {
            key: _llm_scalar(cash.get(key))
            for key in ("balance_yuan", "known", "quality")
            if key in cash
        }
    result["positions"] = [
        {
            key: _llm_scalar(row.get(key))
            for key in _POSITION_TRUTH_ROW_LLM_KEYS
            if key in row
        }
        for row in value.get("positions") or []
        if isinstance(row, Mapping)
    ]
    return result


def _safe_fund_scale_for_llm(row: dict[str, Any]) -> tuple[float, dict[str, Any]] | None:
    """Return scale only with a complete point-in-time provenance envelope.

    A bare AUM number is especially easy for a model to over-weight.  Existing
    snapshots do not always carry the diagnostic fetch metadata, so the safe
    behavior is to omit that number until the caller supplies source, as-of and
    freshness together.  This helper accepts both the new nested envelope and
    flat compatibility keys while emitting one canonical structure.
    """

    raw_value = row.get("fund_scale_yi")
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None

    raw_evidence = row.get("fund_scale_evidence")
    evidence = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
    source = str(evidence.get("source") or row.get("fund_scale_source") or "").strip()
    as_of = str(
        evidence.get("as_of")
        or evidence.get("as_of_date")
        or row.get("fund_scale_as_of")
        or ""
    ).strip()
    freshness = str(
        evidence.get("freshness") or row.get("fund_scale_freshness") or ""
    ).strip().lower()
    if not source or not as_of or freshness not in {
        "fresh",
        "aging",
        "stale",
        "unknown",
        "unavailable",
    }:
        return None

    canonical_evidence: dict[str, Any] = {
        "source": source,
        "as_of": as_of,
        "freshness": freshness,
        # Only explicitly fresh scale may support a strong action.  Aging and
        # stale values can remain visible as background, but never as a trigger.
        "decision_eligible": freshness == "fresh",
    }
    fetched_at = evidence.get("fetched_at") or row.get("fund_scale_fetched_at")
    if fetched_at:
        canonical_evidence["fetched_at"] = str(fetched_at)
    basis = evidence.get("basis") or row.get("fund_scale_basis")
    if basis:
        canonical_evidence["basis"] = str(basis)
    return value, canonical_evidence


def _safe_management_fee_for_llm(value: object) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    return {
        "annual_rate": text,
        "already_reflected_in_nav": True,
        "transaction_fee": False,
    }


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
    seen: set[tuple[str, ...]] = set()
    announcement_topics_by_title: dict[str, set[str]] = {}
    for item in market_news:
        if item.source != "fund-announcement":
            continue
        title = item.title.strip()
        if title:
            announcement_topics_by_title.setdefault(title, set()).add(item.topic)
    for item in items:
        title = item.title.strip()
        identity = _compact_news_identity(
            title=title,
            topic=item.topic,
            source=item.source,
        )
        if not title or identity in seen:
            continue
        seen.add(identity)
        row: dict[str, Any] = {
            "topic": item.topic,
            "title": title,
            "is_today": item.is_today,
        }
        if item.related_topics:
            row["related_topics"] = list(dict.fromkeys(item.related_topics))
        if item.published_at:
            row["published_at"] = item.published_at
        if item.source:
            row["source"] = item.source
        compact.append(row)

    for brief in topic_briefs or []:
        for point in brief.points:
            for raw_title in point.source_titles:
                title = str(raw_title).strip()
                source = (
                    "fund-announcement"
                    if brief.topic in announcement_topics_by_title.get(title, set())
                    else None
                )
                identity = _compact_news_identity(
                    title=title,
                    topic=brief.topic,
                    source=source,
                )
                if not title or identity in seen:
                    continue
                seen.add(identity)
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


def _compact_news_identity(
    *,
    title: str,
    topic: str,
    source: str | None,
) -> tuple[str, ...]:
    if source == "fund-announcement":
        return ("fund-announcement", str(topic).strip(), title)
    return ("title", title)


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
        "style": profile.style,
        "horizon": profile.horizon,
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
    benchmark_specs = facts.get("benchmark_specs")
    if isinstance(benchmark_specs, Mapping):
        trimmed["benchmark_specs"] = {
            str(code): _compact_benchmark_spec_for_llm(spec)
            for code, spec in benchmark_specs.items()
            if isinstance(spec, Mapping)
        }
    announcement_facts = trimmed.get("fund_announcements")
    if isinstance(announcement_facts, dict):
        trimmed["fund_announcements"] = compact_announcement_fetch_status(
            announcement_facts
        )
    # Internal report orchestration evidence; never expose it to an LLM/public payload.
    trimmed.pop("sector_flow_by_label", None)
    trimmed.pop("fund_lookthrough", None)
    trimmed.pop("fund_lookthrough_claim_audit", None)
    holdings = []
    has_management_fee = False
    for row in facts.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        copy = dict(row)
        if "tradeability" in row:
            raw_tradeability = row.get("tradeability")
            copy["tradeability"] = compact_tradeability_for_llm(
                raw_tradeability if isinstance(raw_tradeability, Mapping) else None
            )
        safe_scale = _safe_fund_scale_for_llm(row)
        safe_management_fee = _safe_management_fee_for_llm(row.get("management_fee"))
        for key in _HOLDING_LLM_DROP_KEYS:
            copy.pop(key, None)
        if safe_scale is not None:
            copy["fund_scale_yi"], copy["fund_scale_evidence"] = safe_scale
        if safe_management_fee is not None:
            copy["management_fee_annual_recurring"] = safe_management_fee
            has_management_fee = True
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
    if has_management_fee:
        semantics = trimmed.get("fund_fact_semantics")
        semantic_copy = dict(semantics) if isinstance(semantics, dict) else {}
        semantic_copy["management_fee_annual_recurring"] = _MANAGEMENT_FEE_SEMANTICS
        trimmed["fund_fact_semantics"] = semantic_copy

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
                for k in (
                    "available",
                    "signal_mode",
                    "source_mode",
                    "trade_date",
                    "as_of_datetime",
                    "freshness_status",
                    "decision_eligible",
                    "sentiment_level",
                    "sentiment_level_change",
                    "activity_percent",
                    "advance_count",
                    "decline_count",
                    "interpretation",
                )
                if k in breadth
            }

    position_truth = trimmed.get("portfolio_position_truth")
    if isinstance(position_truth, Mapping):
        trimmed["portfolio_position_truth"] = (
            compact_portfolio_position_truth_for_llm(position_truth)
        )
    snapshot = trimmed.get("portfolio_snapshot")
    if isinstance(snapshot, Mapping):
        trimmed["portfolio_snapshot"] = compact_portfolio_snapshot_for_llm(snapshot)
    evidence = trimmed.get("data_evidence")
    if isinstance(evidence, Mapping):
        trimmed["data_evidence"] = compact_data_evidence_for_llm(evidence)

    return trimmed


def _compact_benchmark_spec_for_llm(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Keep benchmark identity/role/evidence while excluding verbose raw contract text."""

    components: list[dict[str, Any]] = []
    for value in spec.get("components") or []:
        if not isinstance(value, Mapping):
            continue
        components.append(
            {
                key: value.get(key)
                for key in (
                    "component_id",
                    "component_type",
                    "name",
                    "benchmark_code",
                    "weight_percent",
                    "max_lag_calendar_days",
                )
                if value.get(key) is not None
            }
        )
    return {
        key: spec.get(key)
        for key in (
            "schema_version",
            "mapping_id",
            "tier",
            "status",
            "fund_code",
            "benchmark_kind",
            "contract_verification_kind",
            "completeness",
            "benchmark_name",
            "benchmark_code",
            "valid_from",
            "available_at",
            "confidence",
            "formal_excess_eligible",
            "reason",
        )
        if spec.get(key) is not None
    } | {"components": components}


@dataclass
class AnalysisFactsBundle:
    """一次计算的 analysis_facts 上下文，供 prompt 与存档复用。"""

    session: dict
    factor_scores: dict | None
    risk_metrics: dict | None
    portfolio_trend: dict | None
    facts: dict


TradeabilityResolver = Callable[..., dict[str, dict[str, Any]]]
BenchmarkResolver = Callable[..., dict[str, dict[str, Any]]]
BenchmarkResearchResolver = Callable[..., dict[str, dict[str, Any]]]


def _unavailable_holding_benchmark(*, reason: str) -> dict[str, Any]:
    return {
        "schema_version": BENCHMARK_MAPPING_SCHEMA_VERSION,
        "tier": "unavailable",
        "status": "unavailable",
        "formal_excess_eligible": False,
        "mapping_id": None,
        "contract_verification_kind": None,
        "reason": reason,
        "components": [],
    }


def _resolve_holding_benchmark_specs(
    holdings: list,
    *,
    decision_at: datetime | None,
    resolver: BenchmarkResolver,
) -> dict[str, dict[str, Any]]:
    """Resolve cached PIT benchmark roles without making report generation brittle."""

    codes = sorted(
        {
            str(getattr(holding, "fund_code", "") or "").strip().zfill(6)
            for holding in holdings
            if str(getattr(holding, "fund_code", "") or "").strip()
        }
    )
    resolvable = [code for code in codes if code != "000000"]
    try:
        resolved = (
            resolver(
                resolvable,
                decision_at=normalize_news_now(decision_at),
            )
            if resolvable
            else {}
        )
    except Exception:  # noqa: BLE001 - missing mappings fail closed, not fatal
        resolved = {}

    normalized = {
        str(code).strip().zfill(6): dict(row)
        for code, row in (resolved.items() if isinstance(resolved, Mapping) else [])
        if isinstance(row, Mapping)
    }
    return {
        code: normalized.get(code)
        or _unavailable_holding_benchmark(
            reason=(
                "unresolved_fund_code"
                if code == "000000"
                else "point_in_time_benchmark_mapping_unavailable"
            )
        )
        for code in codes
    }


def _benchmark_contract(specs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(value) for value in specs.values() if isinstance(value, Mapping)]
    return {
        "schema_version": BENCHMARK_MAPPING_SCHEMA_VERSION,
        "lookup_policy": "cached_point_in_time_before_generation",
        "formal_excess_policy": "verified_fund_contract_only",
        "reference_policy": "tracked_index_never_formal",
        "formal_count": sum(
            1
            for row in rows
            if row.get("tier") == "fund_contract_exact"
            and row.get("formal_excess_eligible") is True
        ),
        "reference_count": sum(
            1 for row in rows if row.get("tier") == "tracked_index_exact"
        ),
        "unavailable_count": sum(
            1 for row in rows if row.get("tier") == "unavailable"
        ),
    }


def _holding_benchmark_research_rows(
    holdings: list,
    specs: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for holding in holdings:
        code = str(getattr(holding, "fund_code", "") or "").strip().zfill(6)
        if not code or code == "000000":
            continue
        rows.append(
            {
                "fund_code": code,
                "fund_name": str(getattr(holding, "fund_name", "") or code),
                "fund_type": getattr(holding, "fund_type", None),
                "benchmark_spec": dict(specs.get(code) or {}),
            }
        )
    return rows


def _unavailable_benchmark_metrics(
    specs: Mapping[str, Mapping[str, Any]],
    *,
    reason: str,
) -> dict[str, dict[str, Any]]:
    return {
        code: {
            "schema_version": BENCHMARK_RESEARCH_SCHEMA_VERSION,
            "status": "unavailable",
            "qualified": False,
            "descriptive_only": True,
            "execution_tilt_eligible": False,
            "comparison_role": "unavailable",
            "formal_excess_eligible": False,
            "mapping_id": spec.get("mapping_id"),
            "benchmark_code": spec.get("benchmark_code"),
            "benchmark_name": spec.get("benchmark_name"),
            "reason_codes": [reason],
        }
        for code, spec in specs.items()
    }


def _unavailable_holding_tradeability(
    fund_code: str,
    *,
    decision_at: datetime | None,
    reason: str,
) -> dict[str, Any]:
    effective_at = normalize_news_now(decision_at).isoformat()
    result: dict[str, Any] = {
        "schema_version": "fund_tradeability.v1",
        "fund_code": fund_code,
        "data_status": "unavailable",
        "freshness": "unavailable",
        "purchase_state": "unknown",
        "redemption_state": "unknown",
        "currency": "unknown",
        "daily_purchase_limit_unlimited": False,
        "source_conflict": False,
        "missing_fields": ["purchase_status", "redemption_status", "additional_minimum"],
        "source_ids": [],
        "checked_at": None,
        "effective_at": effective_at,
        "revalidation_required": True,
        "unavailable_reason": reason,
        "instruction": "交易条件不可核验，本次不得生成可执行加仓或减仓金额。",
    }
    result["tradeability_gate"] = build_tradeability_gate(result)
    return result


def _resolve_holding_tradeability_profiles(
    holdings: list,
    *,
    decision_at: datetime | None,
    resolver: TradeabilityResolver,
) -> dict[str, dict[str, Any]]:
    codes = sorted(
        {
            str(getattr(holding, "fund_code", "") or "").strip().zfill(6)
            for holding in holdings
            if str(getattr(holding, "fund_code", "") or "").strip()
        }
    )
    resolvable = [code for code in codes if code != "000000"]
    try:
        resolved = resolver(resolvable, decision_at=decision_at) if resolvable else {}
    except Exception:  # noqa: BLE001 - missing tradeability must fail closed, not fail the report
        resolved = {}
    normalized_resolved = {
        str(code).strip().zfill(6): row
        for code, row in (resolved.items() if isinstance(resolved, Mapping) else [])
    }
    output: dict[str, dict[str, Any]] = {}
    for code in codes:
        row = normalized_resolved.get(code)
        output[code] = (
            dict(row)
            if isinstance(row, Mapping)
            else _unavailable_holding_tradeability(
                code,
                decision_at=decision_at,
                reason=("unresolved_fund_code" if code == "000000" else "provider_unavailable"),
            )
        )
    return output


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
    decision_at: datetime | None = None,
) -> tuple[dict, dict | None, dict | None, dict | None]:
    session = build_trading_session(decision_at)
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
    decision_at: datetime | None = None,
    tradeability_resolver: TradeabilityResolver | None = None,
    benchmark_resolver: BenchmarkResolver | None = None,
    benchmark_research_resolver: BenchmarkResearchResolver | None = None,
) -> AnalysisFactsBundle:
    """构建完整 analysis_facts（未 trim），供 LLM prompt 与最终存档各用一次。"""
    briefs = topic_briefs or []
    nav_trends = nav_trends_by_code or {}
    resolver = tradeability_resolver or resolve_fund_tradeability_profiles
    resolve_benchmarks = benchmark_resolver or load_decision_benchmark_specs
    resolve_benchmark_research = (
        benchmark_research_resolver or build_fund_benchmark_research_batch
    )
    user_id = try_get_request_user_id()

    def resolve_tradeability() -> dict[str, dict[str, Any]]:
        def work() -> dict[str, dict[str, Any]]:
            return _resolve_holding_tradeability_profiles(
                request.holdings,
                decision_at=decision_at,
                resolver=resolver,
            )

        return work() if user_id is None else run_with_request_user(user_id, work)

    def resolve_benchmark_context() -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
    ]:
        def work() -> tuple[
            dict[str, dict[str, Any]],
            dict[str, dict[str, Any]],
        ]:
            specs = _resolve_holding_benchmark_specs(
                request.holdings,
                decision_at=decision_at,
                resolver=resolve_benchmarks,
            )
            rows = _holding_benchmark_research_rows(request.holdings, specs)
            try:
                research = resolve_benchmark_research(
                    rows,
                    decision_at=normalize_news_now(decision_at),
                )
            except Exception:  # noqa: BLE001 - research remains descriptive/fail-closed
                research = _unavailable_benchmark_metrics(
                    specs,
                    reason="benchmark_research_provider_unavailable",
                )
            return specs, research

        return work() if user_id is None else run_with_request_user(user_id, work)

    # Tradeability I/O runs alongside the existing context computation, while
    # the latter stays on the request thread so database/request context behavior
    # is unchanged.
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis-evidence")
    tradeability_future = executor.submit(resolve_tradeability)
    benchmark_future = executor.submit(resolve_benchmark_context)
    try:
        session, factor_scores, risk_metrics, portfolio_trend = _compute_analysis_context(
            request.holdings,
            analysis_mode=analysis_mode,
            phase=phase,
            budget_enhancements=budget_enhancements,
            decision_at=decision_at,
        )
        tradeability_profiles = tradeability_future.result()
        benchmark_specs, benchmark_research = benchmark_future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
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
        decision_at=decision_at,
        tradeability_profiles=tradeability_profiles,
    )
    facts["benchmark_specs"] = benchmark_specs
    facts["benchmark_contract"] = _benchmark_contract(benchmark_specs)
    facts["benchmark_research"] = benchmark_research
    facts["benchmark_research_contract"] = summarize_benchmark_research(
        benchmark_research
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
    decision_at: datetime | None = None,
) -> dict:
    """在预计算 facts 上叠加 pipeline / 更新后的 news，避免重复 build_analysis_facts。"""
    facts = dict(base_facts)
    if market_news is not None or topic_briefs is not None:
        facts["news"] = build_news_pipeline_context(
            market_news or [],
            topic_briefs,
            now=decision_at,
        )
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
    decision_at: datetime | None = None,
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
        decision_at=decision_at,
    )
    facts = trim_analysis_facts_for_llm(
        bundle.facts,
        analysis_mode=analysis_mode,
        decision_style=request.profile.decision_style or "conservative",
        phase=phase,
    )

    minimal_briefs = phase >= 2 and analysis_mode == "fast"
    payload: dict = {
        "today": str(
            bundle.session.get("calendar_date")
            or normalize_news_now(decision_at).date().isoformat()
        ),
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
    return system_prompt.rstrip() + "\n\n" + OUTPUT_REQUIREMENTS_SYSTEM
