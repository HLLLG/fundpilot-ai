from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable, Mapping
import re
import threading
import time
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
from app.services.investment_presets import take_profit_threshold_percent
from app.services.analysis_facts import build_analysis_facts
from app.services.holding_metrics import HOLDING_RETURN_SEMANTICS
from app.services.analysis_runtime import AnalysisMode
from app.services.news_freshness import build_news_pipeline_context, normalize_news_now
from app.services.news_service import compact_announcement_fetch_status
from app.services.pipeline_concurrency import run_with_request_user
from app.services.shared_executors import get_shared_io_executor
from app.services.streaming_heartbeat import raise_if_stream_cancelled
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
    attach_compact_fund_benchmark_metrics,
    build_fund_benchmark_research_batch,
    summarize_benchmark_research,
)
from app.services.fund_lookthrough_context import build_fund_lookthrough_context
from app.services.fund_lookthrough_research import (
    LOOKTHROUGH_RESEARCH_SCHEMA_VERSION,
    compact_fund_lookthrough_for_llm,
)
from app.services.fund_vehicle_quality import attach_holding_vehicle_quality
from app.services.report_peer_ranking import (
    attach_holding_peer_research,
    resolve_holding_peer_research,
)
from app.services.fund_tradeability import (
    build_tradeability_gate,
    resolve_fund_tradeability_profiles,
)

AnalysisPayloadPhase = Literal[1, 2, 3]

FACTOR_SCORE_TIMEOUT_SECONDS = 4.0
RISK_METRICS_TIMEOUT_SECONDS = 3.0
# 穿透内部是两段预算：先 store-only 扫描（封顶 `fund_holdings_context_fast_timeout_seconds`，
# 默认 2s），deep 模式再用剩余额度（默认合计 18s）对 aging/stale 的披露做现场刷新。
#
# 日报不能等 18s，所以这里额外套一层 8s 外层预算：store 阶段总能跑完，现场刷新则可能被
# 中途放弃。这是有意的取舍——`future.cancel()` 对已运行的任务无效，被放弃的刷新会继续跑完
# 自己的预算并把结果写进披露存储，所以本次报告标记 unavailable、下一份报告直接命中。
# 换句话说外层预算裁的是"等待"，不是"刷新"本身。
LOOKTHROUGH_TIMEOUT_SECONDS = 8.0

# 同类分位：实测本地加载 20,000 行目录快照约 4.07 s（DB 读 + 反序列化），逐只
# `build_peer_rank` 只有 8~14 ms。所以预算按"目录读得动就行"给，超时即 fail closed
# 到"同类分位不可用"——它是描述性证据，缺席不影响任何确定性结论，不值得让日报多等。
PEER_RESEARCH_TIMEOUT_SECONDS = 6.0

# 迁入 system 的完整输出约束（不再每条请求在 user JSON 重复）
OUTPUT_REQUIREMENTS_SYSTEM = (
    "analysis_facts.portfolio_position_truth 是持仓份额、成本和现金的唯一真值摘要；"
    "unknown/null 不得按 0 猜测。日报统一采用相对当前估算持仓的百分比建议，amount_yuan 必须始终为 null，"
    "不得自行计算份额或固定金额；suggested_position_change_percent 由服务端确定性规则生成，模型须省略或输出 null。"
    "estimated_position_change_amount_yuan 同样由服务端按最终比例和报告生成时持仓估值折算，模型不得输出。"
    "position_complete=false、ledger_truncated=true 或存在 pending/conflict 不阻断百分比方向建议；"
    "只要持仓金额与市场方向证据新鲜可用，仍须从 allowed_actions 中给出加仓、减仓或观察。"
    "输出必须是完整 JSON（不要 Markdown），包含 title、summary、fund_recommendations、caveats。"
    "fund_recommendations 每只持仓基金恰好 1 条；必填字段：fund_code、fund_name、action、"
    "points（1-2 条，每条≤60字）、confidence（高/中/低）、risks（1 条即可）。"
    "amount_yuan 必须为 null；amount_note、hold_horizon、news_bullish、news_bearish 可省略。"
    "不要输出 decision_path、sector_evidence、fund_evidence、validation_notes；"
    "这些由服务端从 analysis_facts 补全，写了也只增加篇幅、不改变动作。"
    "points 只写该持仓特有的因果（板块涨跌/资金/浮亏/载体是否落后板块），"
    "禁止复述最终动作或写「系统校验后的最终动作」，禁止猜测赎回费/锁定期（服务端会给系统提示）。"
    "news_bullish 与 news_bearish 若输出必须是字符串 JSON 数组；无匹配新闻时输出 [] 或省略，"
    "禁止写「暂无明确利好」或「暂无明确利空」。"
    "caveats 与 recommendations 同样必须是字符串 JSON 数组（如 [\"提示\"]），"
    "即使只有一条也要写成单元素数组，禁止写成单个字符串。"
    "利好/利空标题须能在 news_titles 或 topic_briefs.points.source_titles 中找到对应。"
    "须遵循 analysis_facts.session.decision_window 与 session_kind 调整措辞，"
    "非 trading_day_pre_close 时不要写「收盘前必须今日下单」。"
    "action 的唯一合法集合是 analysis_facts.allowed_actions；必须逐字从该数组选择，不得依赖固定数量或另造动作。"
    "服务端另有一套确定性动作提议（方向成熟度 + 量化证据 + 风险升级 + 集中度/交易门禁），"
    "它可能用系统提议替换你给出的 action，并在校验备注中说明分歧。"
    "因此你的职责重心是用 1-2 条 points 讲清「为什么是这个方向」，"
    "而不是替系统决定动作，也不是复述板块机会卡里已有的数字；仍须按上述规则给出你的 action 判断，"
    "但不要在叙述里承诺「必须按我给的动作执行」，也不要因为担心被改写而刻意含糊或一律写观察。"
    "若 analysis_facts.portfolio.suggested_action 为 risk_review 或 risk_level 为 high，禁止加仓类 action。"
    "analysis_facts.holdings[].transaction_execution 是交易执行硬门禁："
    "分批加仓仅在 add_status=eligible 时才可输出，具体比例由服务端按板块机会分分档，并结合追加起购额、单日限额与集中度收紧；"
    "减仓类动作即使 redemption_status=eligible，也不得猜测逐笔持有期、锁定期或赎回费；"
    "acquisition_lot_status=unverified 时仍可给减仓方向，不要在 points 里写赎回费或锁定期。"
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
    "sector_fund_flow.today_main_force_net_yi：正=主力净流入、负=主力净流出。"
    "量价背离结论只能引用系统给出的 pattern_label/pattern_hint——它由东财板块口径内的"
    "同源数据算出（价格腿为 flow_price_change_percent，不是 sector_return_percent；"
    "后者是主题指数口径，两者成分篮子不同、同日可差数个百分点），禁止自行拿"
    " sector_return_percent 与主力净流入推断背离。"
    "date_aligned=false 或 pattern_label=flow_date_mismatch/price_source_mismatch 时"
    "禁止写出货/诱多等背离结论。"
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
    "analysis_facts.data_evidence.decision_ready=false 或 blocking_reasons 非空时，"
    "不得给出加仓/减仓类动作；is_estimate=true 的收益数字必须写为估算。"
    "news_titles 中 source=cls 为财联社快讯。若 nav_trend 为空须在 points 说明。"
    "analysis_facts.market_breadth 是大盘情绪温度计：decision_eligible=false 或"
    "freshness_status=stale 时只能作背景；不得混称盘中情绪与收盘历史百分位。"
    "holdings[].flow_divergence_backtest 只在 significant=true 时可作方向辅助，"
    "不得单独作为加仓或减仓的唯一依据。"
    "不要声称跑赢基准或引用同类分位；未提供的穿透数字不得编造。"
)

OUTPUT_REQUIREMENTS_USER = [
    "portfolio_position_truth 中 unknown/null 不得按 0；amount_yuan 始终为空，比例与估算调整金额均留空交由服务端计算；份额未确认不阻断百分比方向判断",
    "analysis_facts 为系统计算的只读事实，不得改写其中任何数字",
    "输出 title、summary、fund_recommendations、caveats；每只基金恰好 1 条 recommendation",
    "action 仅限 analysis_facts.allowed_actions；risk_review 或 high 禁止加仓类",
    "news_bullish/news_bearish 可省略或为空数组；若写须来自 news_titles，禁止占位句",
    "每只基金 points 1-2 条：写该持仓特有因果，且其中 1 条写下一交易日条件化预案；禁止复述最终动作",
    "引用 sector_intraday.pattern_label、nav_trend、sector_fund_gap_percent、sector_fund_flow 时须用 analysis_facts 中的数字",
    "每只基金须含 confidence、risks（1条即可）；不要输出 decision_path/sector_evidence/fund_evidence/validation_notes",
]

BENCHMARK_OUTPUT_REQUIREMENTS_SYSTEM = (
    "不要声称跑赢基准或跟踪良好；未提供的基准数字不得编造，也不得据此调整仓位。"
)
LOOKTHROUGH_OUTPUT_REQUIREMENTS_SYSTEM = (
    "fund_lookthrough 仅在 research_eligible=true 时可引用 portfolio.top_* 暴露下界，"
    "须写成「组合暴露下界≥X%」并说明来自定期报告、有滞后；不得写成两只基金重合度，"
    "也不得把未发现共同证券说成组合分散或作为加仓理由。"
)
OUTPUT_REQUIREMENTS_SYSTEM = (
    OUTPUT_REQUIREMENTS_SYSTEM
    + "\n"
    + BENCHMARK_OUTPUT_REQUIREMENTS_SYSTEM
    + "\n"
    + LOOKTHROUGH_OUTPUT_REQUIREMENTS_SYSTEM
)
OUTPUT_REQUIREMENTS_USER.append(
    "不要声称跑赢基准或引用同类分位；穿透只在 research_eligible 时用组合暴露下界，不得编造重合度"
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
# 描述性/执行门禁明细已由服务端消化；再喂模型只会占 token 或诱发误用。
_HOLDING_LLM_ALWAYS_DROP_KEYS = frozenset(
    {
        "peer_research",
        "tradeability",
        "signal_backtest",
        "benchmark_metrics",
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
    *,
    fund_codes: set[str] | None = None,
) -> dict[str, Any] | None:
    """Project the evidence registry through a scalar-only field allow-list."""

    if not isinstance(value, Mapping):
        return None
    normalized_codes = (
        {str(code).strip().zfill(6) for code in fund_codes if str(code).strip()}
        if fund_codes is not None
        else None
    )

    def include(item: Mapping[str, Any]) -> bool:
        if normalized_codes is None:
            return True
        fact_id = str(item.get("fact_id") or "")
        if not fact_id.startswith("candidates."):
            return True
        parts = fact_id.split(".", 2)
        return len(parts) >= 2 and parts[1].zfill(6) in normalized_codes

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
            if isinstance(item, Mapping) and include(item)
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


def _is_fund_code_topic(topic: str | None) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(topic or "").strip()))


def compact_news_titles(
    market_news: list[NewsItem],
    topic_briefs: list[TopicBrief] | None = None,
    *,
    today_only: bool = True,
    max_items: int = 20,
    min_items: int = 12,
    include_announcements: bool = True,
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
    if not include_announcements:
        items = [item for item in items if item.source != "fund-announcement"]
    compact: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    announcement_topics_by_title: dict[str, set[str]] = {}
    if include_announcements:
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
        if not include_announcements and _is_fund_code_topic(brief.topic):
            continue
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
    exclude_fund_code_topics: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for brief in briefs:
        if exclude_fund_code_topics and _is_fund_code_topic(brief.topic):
            continue
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
    return {
        "prefer_dca": profile.prefer_dca,
        "avoid_chasing": profile.avoid_chasing,
        "max_drawdown_percent": profile.max_drawdown_percent,
        "concentration_limit_percent": profile.concentration_limit_percent,
        "expected_investment_amount": profile.expected_investment_amount,
        "round_trip_fee_percent": profile.round_trip_fee_percent,
        "min_net_profit_percent": profile.min_net_profit_percent,
        "take_profit_threshold_percent": take_profit_threshold_percent(profile),
        "hold_days_target": profile.hold_days_target,
    }


def _pick(mapping: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        return {}
    return {key: mapping[key] for key in keys if key in mapping}


def _compact_transaction_execution_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return _pick(
        value,
        (
            "add_status",
            "redemption_status",
            "reduction_amount_status",
            "acquisition_lot_status",
            "add_block_reasons",
            "redemption_block_reasons",
        ),
    ) or None


def _compact_direction_exit_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    compact = _pick(
        value,
        (
            "exit_state",
            "allows_add",
            "min_action_label",
            "suggested_position_change_percent",
            "basis",
            "sector_label",
            "thresholds_validated",
            "trend_strength",
            "consecutive_days_below_exit_line",
        ),
    )
    reasons = [str(item).strip() for item in (value.get("reasons") or []) if str(item).strip()]
    if reasons:
        compact["reasons"] = reasons[:3]
    promises = [
        item
        for item in (value.get("breached_entry_promises") or [])
        if isinstance(item, (str, Mapping))
    ]
    if promises:
        compact["breached_entry_promises"] = promises[:3]
    reference = value.get("entry_reference")
    if isinstance(reference, Mapping):
        compact["entry_reference"] = _pick(
            reference,
            ("entry_trend_strength", "current_trend_strength", "sector_label"),
        )
    return compact or None


def _compact_escalation_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    compact = _pick(
        value,
        ("min_action_label", "suggested_position_change_percent", "basis"),
    )
    reasons = [str(item).strip() for item in (value.get("reasons") or []) if str(item).strip()]
    if reasons:
        compact["reasons"] = reasons[:3]
    return compact or None


def _compact_holding_evidence_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    composite = value.get("composite")
    level = None
    if isinstance(composite, Mapping):
        level = composite.get("level")
    compact: dict[str, Any] = {}
    if level is not None:
        compact["composite_level"] = level
    if value.get("schema_version"):
        compact["schema_version"] = value.get("schema_version")
    return compact or None


def _compact_vehicle_quality_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    compact = _pick(value, ("applicable", "status"))
    reasons = [str(item).strip() for item in (value.get("reasons") or []) if str(item).strip()]
    penalties = [str(item).strip() for item in (value.get("penalties") or []) if str(item).strip()]
    if reasons:
        compact["reasons"] = reasons[:4]
    if penalties:
        compact["penalties"] = penalties[:2]
    return compact or None


def _compact_lot_maturity_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return _pick(
        value,
        ("available", "short_hold_share_percent", "next_penalty_free_date", "coverage"),
    ) or None


def _compact_recent_transactions_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    compact = _pick(value, ("available", "buy_count", "sell_count"))
    for key in ("last_buy", "last_sell"):
        event = value.get(key)
        if isinstance(event, Mapping):
            compact[key] = _pick(event, ("trade_date", "confirm_date", "amount_yuan"))
    return compact or None


def _compact_sector_fund_flow_for_llm(value: Mapping[str, Any]) -> dict[str, Any] | None:
    return _pick(
        value,
        (
            "date_aligned",
            "pattern_label",
            "pattern_hint",
            "flow_tiers",
            "flow_structure_hint",
            "flow_price_change_percent",
            "cumulative_20d_net_yi",
        ),
    ) or None


def _compact_flow_divergence_for_llm(value: Mapping[str, Any]) -> dict[str, Any] | None:
    significant: list[dict[str, Any]] = []
    by_rule = value.get("by_rule")
    if isinstance(by_rule, Mapping):
        for raw in by_rule.values():
            if not isinstance(raw, Mapping) or raw.get("significant") is not True:
                continue
            significant.append(
                _pick(
                    raw,
                    (
                        "rule_id",
                        "label",
                        "trigger_count",
                        "hit_rate_percent",
                        "edge_percent",
                        "significant",
                    ),
                )
            )
    compact: dict[str, Any] = {"resolved": value.get("resolved"), "significant": bool(significant)}
    if significant:
        compact["by_rule"] = significant
    return compact


def _compact_sector_rotation_for_llm(value: Mapping[str, Any]) -> dict[str, Any]:
    market_top: list[dict[str, Any]] = []
    for item in (value.get("market_top") or [])[:3]:
        if not isinstance(item, Mapping):
            continue
        compact = _pick(
            item,
            (
                "sector_label",
                "track",
                "score",
                "confidence",
                "entry_state",
                "today_main_force_net_yi",
            ),
        )
        if compact:
            market_top.append(compact)
    return {"available": value.get("available", False), "market_top": market_top}


def _compact_factor_scores_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    ic_status = value.get("ic_status")
    state = ic_status.get("state") if isinstance(ic_status, Mapping) else None
    return {"ic_status": {"state": state or "unavailable"}}


def _compact_lookthrough_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    decision_use = value.get("decision_use") if isinstance(value.get("decision_use"), Mapping) else {}
    research_eligible = (
        value.get("research_qualified") is True
        or decision_use.get("research_eligible") is True
    )
    compact: dict[str, Any] = {
        "status": value.get("status"),
        "research_eligible": research_eligible,
        "execution_qualified": value.get("execution_qualified") is True,
        "reason_codes": [
            str(item).strip()
            for item in (value.get("reason_codes") or [])
            if str(item).strip()
        ][:4],
    }
    if not research_eligible:
        return compact
    portfolio = value.get("portfolio") if isinstance(value.get("portfolio"), Mapping) else {}
    compact["portfolio"] = {
        key: portfolio.get(key)
        for key in (
            "unknown_account_mass_percent",
            "top_security_exposure_lower_bounds",
            "top_industry_exposure_lower_bounds",
            "top_listing_market_exposure_lower_bounds",
        )
        if key in portfolio
    }
    return compact


def _compact_daily_action_proposal_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    rows: list[dict[str, Any]] = []
    for item in value.get("by_fund") or []:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            _pick(item, ("fund_code", "action", "reason_codes", "supports_add"))
        )
    return {
        "mode": value.get("mode"),
        "by_fund": rows,
    }


def _compact_position_truth_status_for_llm(value: Mapping[str, Any]) -> dict[str, Any]:
    compact = _pick(
        value,
        (
            "position_complete",
            "position_truth_status",
            "ledger_truncated",
            "pending_transaction_count",
            "conflict_count",
        ),
    )
    cash = value.get("cash")
    if isinstance(cash, Mapping):
        compact["cash_known"] = cash.get("known")
    return compact


def _compact_data_evidence_status_for_llm(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision_ready": value.get("decision_ready"),
        "blocking_reasons": [
            str(item).strip()
            for item in (value.get("blocking_reasons") or [])
            if str(item).strip()
        ][:6],
    }


def _compact_sector_direction_maturity_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return _pick(
        value,
        ("available", "complete", "missing_labels", "hysteresis_applied"),
    ) or None


def _compact_discovery_cross_reference_for_llm(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    compact = _pick(value, ("available", "buy_recommendations_by_sector"))
    return compact or None


def trim_analysis_facts_for_llm(
    facts: dict[str, Any],
    *,
    analysis_mode: AnalysisMode = "deep",
    phase: AnalysisPayloadPhase = 3,
) -> dict[str, Any]:
    trimmed = dict(facts)
    announcement_facts = trimmed.get("fund_announcements")
    if isinstance(announcement_facts, dict):
        trimmed["fund_announcements"] = compact_announcement_fetch_status(
            announcement_facts
        )
    trimmed.pop("sector_flow_by_label", None)
    trimmed.pop("fund_lookthrough_claim_audit", None)
    trimmed.pop("benchmark_research", None)
    trimmed.pop("benchmark_specs", None)
    trimmed.pop("benchmark_research_contract", None)
    trimmed.pop("benchmark_contract", None)
    trimmed.pop("transaction_behavior_review", None)
    holdings = []
    has_management_fee = False
    for row in facts.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        copy = dict(row)
        safe_scale = _safe_fund_scale_for_llm(row)
        safe_management_fee = _safe_management_fee_for_llm(row.get("management_fee"))
        for key in _HOLDING_LLM_DROP_KEYS | _HOLDING_LLM_ALWAYS_DROP_KEYS:
            copy.pop(key, None)
        if safe_scale is not None:
            copy["fund_scale_yi"], copy["fund_scale_evidence"] = safe_scale
        if safe_management_fee is not None:
            copy["management_fee_annual_recurring"] = safe_management_fee
            has_management_fee = True
        copy["transaction_execution"] = _compact_transaction_execution_for_llm(
            copy.get("transaction_execution")
        )
        copy["direction_exit"] = _compact_direction_exit_for_llm(copy.get("direction_exit"))
        copy["escalation"] = _compact_escalation_for_llm(copy.get("escalation"))
        copy["evidence"] = _compact_holding_evidence_for_llm(copy.get("evidence"))
        copy["vehicle_quality"] = _compact_vehicle_quality_for_llm(copy.get("vehicle_quality"))
        copy["lot_maturity"] = _compact_lot_maturity_for_llm(copy.get("lot_maturity"))
        copy["recent_transactions"] = _compact_recent_transactions_for_llm(
            copy.get("recent_transactions")
        )
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
                copy["sector_intraday"] = _pick(
                    intraday,
                    (
                        "pattern_label",
                        "pattern_hint",
                        "close_change_percent",
                        "pullback_from_high_percent",
                    ),
                ) or None
            sector_flow = copy.get("sector_fund_flow")
            if isinstance(sector_flow, dict) and phase >= 2:
                copy["sector_fund_flow"] = _compact_sector_fund_flow_for_llm(sector_flow)
            sector_opportunity = copy.get("sector_opportunity")
            if isinstance(sector_opportunity, dict):
                opportunity_copy = {
                    k: v for k, v in sector_opportunity.items() if k != "sector_group"
                }
                if phase >= 2:
                    opportunity_copy = _pick(
                        opportunity_copy,
                        (
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
                            "entry_state",
                            "entry_reason",
                            "first_tranche_scale",
                            "trend_formation_probability",
                            "waiting_reason_code",
                            "overheat_flags",
                        ),
                    )
                copy["sector_opportunity"] = opportunity_copy or None
            divergence = copy.get("flow_divergence_backtest")
            if isinstance(divergence, dict) and phase >= 2:
                copy["flow_divergence_backtest"] = _compact_flow_divergence_for_llm(
                    divergence
                )
        holdings.append({key: value for key, value in copy.items() if value is not None})
    trimmed["holdings"] = holdings
    if has_management_fee:
        semantics = trimmed.get("fund_fact_semantics")
        semantic_copy = dict(semantics) if isinstance(semantics, dict) else {}
        semantic_copy["management_fee_annual_recurring"] = _MANAGEMENT_FEE_SEMANTICS
        trimmed["fund_fact_semantics"] = semantic_copy

    news = trimmed.get("news")
    if isinstance(news, dict) and phase >= 1:
        trimmed["news"] = {k: news[k] for k in news if k != "topics"}

    rotation = trimmed.get("sector_rotation")
    if isinstance(rotation, dict) and phase >= 1:
        trimmed["sector_rotation"] = _compact_sector_rotation_for_llm(rotation)

    if phase >= 2:
        guard = trimmed.get("guard_policy")
        if isinstance(guard, dict):
            trimmed["guard_policy"] = _pick(
                guard,
                ("enforce_reversal_block", "enforce_pullback_block", "reason"),
            )

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

    if phase >= 2 and isinstance(trimmed.get("signal_backtest"), dict):
        backtest = trimmed["signal_backtest"]
        trimmed["signal_backtest"] = {
            "enabled": backtest.get("enabled"),
            "has_data": backtest.get("has_data"),
            "summary_lines": (backtest.get("summary_lines") or [])[:2],
        }

    if phase >= 2 and isinstance(trimmed.get("market_breadth"), dict):
        breadth = trimmed["market_breadth"]
        if breadth.get("available"):
            trimmed["market_breadth"] = _pick(
                breadth,
                (
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
                ),
            )

    position_truth = trimmed.get("portfolio_position_truth")
    if isinstance(position_truth, Mapping):
        trimmed["portfolio_position_truth"] = _compact_position_truth_status_for_llm(
            position_truth
        )
    snapshot = trimmed.get("portfolio_snapshot")
    if isinstance(snapshot, Mapping):
        trimmed["portfolio_snapshot"] = compact_portfolio_snapshot_for_llm(snapshot)
    evidence = trimmed.get("data_evidence")
    if isinstance(evidence, Mapping):
        trimmed["data_evidence"] = _compact_data_evidence_status_for_llm(evidence)
    lookthrough = trimmed.get("fund_lookthrough")
    if lookthrough is not None:
        trimmed["fund_lookthrough"] = _compact_lookthrough_for_llm(lookthrough)
    factor_scores = trimmed.get("factor_scores")
    if factor_scores is not None:
        trimmed["factor_scores"] = _compact_factor_scores_for_llm(factor_scores)
    proposal = trimmed.get("daily_action_proposal")
    if proposal is not None:
        trimmed["daily_action_proposal"] = _compact_daily_action_proposal_for_llm(proposal)
    maturity = trimmed.get("sector_direction_maturity")
    if maturity is not None:
        trimmed["sector_direction_maturity"] = _compact_sector_direction_maturity_for_llm(
            maturity
        )
    cross_ref = trimmed.get("discovery_cross_reference")
    if cross_ref is not None:
        trimmed["discovery_cross_reference"] = _compact_discovery_cross_reference_for_llm(
            cross_ref
        )

    trimmed.pop("instruction", None)
    trimmed.pop("pipeline", None)

    return trimmed


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
LookthroughResolver = Callable[..., dict[str, Any]]
PeerResearchResolver = Callable[..., dict[str, dict[str, Any]]]


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


def _unavailable_fund_lookthrough(
    *,
    decision_at: datetime | None,
    reason: str,
) -> dict[str, Any]:
    """穿透缺席时也要留下同形状的 fail-closed 事实，而不是让键直接消失。

    键存在且 `status=unavailable`，下游才能区分"这只组合拿不到披露证据"与"这条链路
    忘了算穿透"；`decision_data_evidence` 也据此产出 confidence=none 的证据项。
    """

    return {
        "schema_version": LOOKTHROUGH_RESEARCH_SCHEMA_VERSION,
        "status": "unavailable",
        "scope": "portfolio_only",
        "decision_at": (
            normalize_news_now(decision_at).isoformat() if decision_at is not None else None
        ),
        "research_qualified": False,
        "execution_qualified": False,
        "reason_codes": [reason],
        "portfolio": {},
        "existing_funds": [],
        "candidates": [],
        "raw_holdings_included": False,
        "raw_snapshots_included": False,
    }


def _await_within_budget(
    future,
    *,
    timeout_seconds: float,
    on_timeout: Callable[[], dict[str, Any]],
    on_error: Callable[[], dict[str, Any]],
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    """给一个已提交的 future 套外层预算，超时与异常分别落到各自的兜底事实。

    与 `_run_budgeted_enhancement` 的区别是它不负责提交——调用方需要先并发提交多个
    future、再逐个按各自预算收口，而不是提交完立刻串行等待。超时与异常不合并成同一个
    原因码，否则运维分不清"数据源慢"和"数据源坏"。
    """

    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            raise_if_stream_cancelled(stop_event)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                return on_timeout()
            try:
                return future.result(timeout=min(0.25, remaining))
            except FutureTimeoutError:
                continue
    except Exception:  # noqa: BLE001 - 研究性增强，失败不阻塞日报
        raise_if_stream_cancelled(stop_event)
        return on_error()


def _run_budgeted_enhancement(
    func,
    *,
    timeout_seconds: float,
    fallback: dict[str, Any],
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    user_id = try_get_request_user_id()

    def run():
        if user_id is None:
            return func()
        return run_with_request_user(user_id, func)

    executor = get_shared_io_executor()
    future = executor.submit(run)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            raise_if_stream_cancelled(stop_event)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                return fallback
            try:
                return future.result(timeout=min(0.25, remaining))
            except FutureTimeoutError:
                continue
    except Exception:  # noqa: BLE001 - enhancement facts are best-effort
        raise_if_stream_cancelled(stop_event)
        return _enhancement_unavailable("error")
    finally:
        future.cancel()


def _compute_analysis_context(
    holdings: list,
    *,
    analysis_mode: AnalysisMode = "deep",
    phase: AnalysisPayloadPhase = 3,
    budget_enhancements: bool = False,
    decision_at: datetime | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[dict, dict | None, dict | None, dict | None]:
    raise_if_stream_cancelled(stop_event)
    session = build_trading_session(decision_at)
    include_portfolio_trend = not (phase >= 2 and analysis_mode == "fast")
    try:
        if budget_enhancements:
            factor_scores = _run_budgeted_enhancement(
                lambda: build_factor_scores_for_facts(holdings),
                timeout_seconds=FACTOR_SCORE_TIMEOUT_SECONDS,
                fallback=_enhancement_unavailable("timeout"),
                stop_event=stop_event,
            )
        else:
            factor_scores = build_factor_scores_for_facts(holdings)
    except Exception:  # noqa: BLE001 — best-effort，绝不阻塞日报
        factor_scores = None

    history_rows = None
    portfolio_trend = None
    risk_metrics = None
    try:
        raise_if_stream_cancelled(stop_event)
        from app.database import list_portfolio_daily_snapshots

        history_rows = list_portfolio_daily_snapshots(limit=400)
        if include_portfolio_trend:
            portfolio_trend = build_portfolio_trend_context(history_rows=history_rows)
        if budget_enhancements:
            risk_metrics = _run_budgeted_enhancement(
                lambda: build_risk_metrics_for_facts(history_rows, holdings),
                timeout_seconds=RISK_METRICS_TIMEOUT_SECONDS,
                fallback=_enhancement_unavailable("timeout"),
                stop_event=stop_event,
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
    lookthrough_resolver: LookthroughResolver | None = None,
    peer_research_resolver: PeerResearchResolver | None = None,
    stop_event: threading.Event | None = None,
) -> AnalysisFactsBundle:
    """构建完整 analysis_facts（未 trim），供 LLM prompt 与最终存档各用一次。"""
    briefs = topic_briefs or []
    nav_trends = nav_trends_by_code or {}
    resolver = tradeability_resolver or resolve_fund_tradeability_profiles
    resolve_benchmarks = benchmark_resolver or load_decision_benchmark_specs
    resolve_benchmark_research = (
        benchmark_research_resolver or build_fund_benchmark_research_batch
    )
    resolve_lookthrough = lookthrough_resolver or build_fund_lookthrough_context
    resolve_peer = peer_research_resolver or resolve_holding_peer_research
    user_id = try_get_request_user_id()
    raise_if_stream_cancelled(stop_event)

    def resolve_tradeability() -> dict[str, dict[str, Any]]:
        def work() -> dict[str, dict[str, Any]]:
            raise_if_stream_cancelled(stop_event)
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
            raise_if_stream_cancelled(stop_event)
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

    def resolve_portfolio_lookthrough() -> dict[str, Any]:
        def work() -> dict[str, Any]:
            raise_if_stream_cancelled(stop_event)
            # 日报只穿透已持仓，没有候选基金，因此 candidate_pool 传 None：
            # `build_fund_lookthrough_context` 会把 scope 定为 portfolio_only。
            return resolve_lookthrough(
                request.holdings,
                None,
                decision_at=normalize_news_now(decision_at),
                analysis_mode=analysis_mode,
                portfolio_context=request.portfolio_snapshot_context,
            )

        return work() if user_id is None else run_with_request_user(user_id, work)

    # Tradeability I/O runs alongside the existing context computation, while
    # the latter stays on the request thread so database/request context behavior
    # is unchanged.
    def resolve_peer_research() -> dict[str, Any]:
        def work() -> dict[str, Any]:
            raise_if_stream_cancelled(stop_event)
            return resolve_peer(
                request.holdings,
                decision_at=normalize_news_now(decision_at),
            )

        return work() if user_id is None else run_with_request_user(user_id, work)

    executor = get_shared_io_executor()
    tradeability_future = executor.submit(resolve_tradeability)
    benchmark_future = executor.submit(resolve_benchmark_context)
    lookthrough_future = executor.submit(resolve_portfolio_lookthrough)
    peer_research_future = executor.submit(resolve_peer_research)
    try:
        raise_if_stream_cancelled(stop_event)
        session, factor_scores, risk_metrics, portfolio_trend = _compute_analysis_context(
            request.holdings,
            analysis_mode=analysis_mode,
            phase=phase,
            budget_enhancements=budget_enhancements,
            decision_at=decision_at,
            stop_event=stop_event,
        )
        while True:
            raise_if_stream_cancelled(stop_event)
            try:
                tradeability_profiles = tradeability_future.result(timeout=0.25)
                break
            except FutureTimeoutError:
                continue
        while True:
            raise_if_stream_cancelled(stop_event)
            try:
                benchmark_specs, benchmark_research = benchmark_future.result(
                    timeout=0.25
                )
                break
            except FutureTimeoutError:
                continue
        fund_lookthrough = _await_within_budget(
            lookthrough_future,
            timeout_seconds=LOOKTHROUGH_TIMEOUT_SECONDS,
            on_timeout=lambda: _unavailable_fund_lookthrough(
                decision_at=decision_at,
                reason="lookthrough_context_timeout",
            ),
            on_error=lambda: _unavailable_fund_lookthrough(
                decision_at=decision_at,
                reason="lookthrough_context_error",
            ),
            stop_event=stop_event,
        )
        peer_research = _await_within_budget(
            peer_research_future,
            timeout_seconds=PEER_RESEARCH_TIMEOUT_SECONDS,
            on_timeout=lambda: {},
            on_error=lambda: {},
            stop_event=stop_event,
        )
    finally:
        peer_research_future.cancel()
        tradeability_future.cancel()
        benchmark_future.cancel()
        lookthrough_future.cancel()
    raise_if_stream_cancelled(stop_event)
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
        stop_event=stop_event,
    )
    facts["benchmark_specs"] = benchmark_specs
    facts["benchmark_contract"] = _benchmark_contract(benchmark_specs)
    facts["benchmark_research"] = benchmark_research
    facts["benchmark_research_contract"] = summarize_benchmark_research(
        benchmark_research
    )
    # 把紧凑基准投影挂回持仓行。此前 `benchmark_research` 只以代码为 key 平铺在顶层，
    # 模型必须自己按 fund_code 做一次 join 才能把"这只基金 vs 它的基准"对上——这类
    # 跨表关联正是最容易串行的地方。挂到行内后与荐基候选行同形，也让后续接入
    # `fund_vehicle_quality` 的被动跟踪质量分不必再改这条链路。
    facts["holdings"] = attach_compact_fund_benchmark_metrics(
        facts.get("holdings") or [],
        benchmark_research,
    )
    # 载体质量：必须排在基准挂载之后——被动载体分的跟踪质量分量读的就是行内
    # `benchmark_metrics.tracking_metrics`，提前调用会让每只指数持仓都拿到"样本未形成"
    # 的中性分。这是日报第一次对"这只基金作为投资工具本身合不合格"给出判断（此前只有
    # 板块方向与基金量化证据两个维度）。主动持仓在这里显式返回 not_applicable，
    # 不是低分，理由见 assess_holding_vehicle_quality 的 docstring。
    facts["holdings"] = attach_holding_vehicle_quality(facts["holdings"])
    # 同类分位：严格描述性证据，只进 prompt 与展示，不参与仓位比例、不参与动作拦截
    # （`execution_tilt_eligible` 恒为 False）。超时/缺缓存时逐只标记不可用，
    # 让模型能区分"同类里不占优"与"同类分位算不出来"。
    facts["holdings"] = attach_holding_peer_research(
        facts["holdings"],
        peer_research if isinstance(peer_research, dict) else {},
    )
    # 基金持仓穿透：日报唯一能看到"跨基金重复暴露"的地方。按基金市值算的集中度看不出
    # 三只名字/板块标签都不同的基金其实重仓同一批股票。
    #
    # 完整载荷只在此处短暂存在，供 `attach_analysis_data_evidence` 逐只披露快照生成
    # 时点证据（它需要 `resolution_audit.rows` 与 `existing_funds[].snapshot`）。
    facts["fund_lookthrough"] = (
        fund_lookthrough
        if isinstance(fund_lookthrough, dict)
        else _unavailable_fund_lookthrough(
            decision_at=decision_at,
            reason="lookthrough_context_unavailable",
        )
    )
    facts = attach_analysis_data_evidence(
        facts,
        holdings=request.holdings,
        snapshots=snapshots,
        portfolio_context=request.portfolio_snapshot_context,
    )
    # 证据取完后收敛为该契约自带的有界摘要，落库、回传前端、喂 LLM 全部用这一份形状。
    # 保留完整载荷会有两个代价：`resolution_audit.rows` 与逐只 snapshot 随每份日报持久化
    # 并整体回传；以及"落库形状"与"prompt 形状"字段名不同（`security_exposure_lower_bounds`
    # vs `top_security_exposure_lower_bounds`），任何消费方都得先判断自己拿到的是哪一种。
    facts["fund_lookthrough"] = compact_fund_lookthrough_for_llm(
        facts["fund_lookthrough"]
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
        "news_titles": compact_news_titles(
            prefetched_news,
            briefs,
            include_announcements=False,
        ),
        "topic_briefs": compact_topic_briefs(
            briefs,
            minimal=minimal_briefs,
            exclude_fund_code_topics=True,
        ),
        "requirements": list(OUTPUT_REQUIREMENTS_USER),
    }
    if operator_notes:
        payload["operator_notes"] = list(operator_notes)
    return payload


def append_output_requirements_to_system(system_prompt: str) -> str:
    return system_prompt.rstrip() + "\n\n" + OUTPUT_REQUIREMENTS_SYSTEM
