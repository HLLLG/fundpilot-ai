from __future__ import annotations

import re

from app.config import get_settings
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.services.decision_guard_shared import (
    append_unique as _append_unique,
    fmt_num as _fmt_num,
    humanize_evidence_text as _humanize_evidence_text,
    normalize_confidence_label as _normalize_confidence,
    pattern_label as _pattern_label,
    track_label as _track_label,
)
from app.services.market_signal import has_today_market_signal
from app.services.investment_presets import is_short_term_style
from app.services.signal_guard_policy import resolve_signal_guard_policy
from app.services.recommendations import build_offline_fund_recommendation
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_intraday_summary import summarize_sector_intraday_for_holding
from app.services.sector_momentum import build_sector_momentum_context

# 动作激进度：数值越低越保守（减仓/复核 < 观察 < 暂停 < 加仓）
_ACTION_BUCKET = {
    "reduce": 0,
    "watch": 1,
    "pause": 2,
    "add": 3,
}

_BUCKET_TO_LABEL = {
    "reduce": "减仓评估",
    "watch": "观察",
    "pause": "暂停追涨",
    "add": "分批加仓",
}


_REPORT_HUMANIZE_TEXT_REPLACEMENTS = (
    ("sector_opportunity", "持仓板块方向判断"),
    ("sector_rotation", "板块轮动参考"),
    ("market_top", "更强轮动方向"),
    ("opportunity_available", "机会是否成立"),
    ("factor_reliability", "因子置信"),
    ("risk_metrics", "组合风险指标"),
    ("evidence_overview", "组合证据体检"),
)


def apply_recommendation_guards(
    fund_recs: list[FundRecommendation],
    portfolio_lines: list[str],
    request: AnalysisRequest,
    risk: RiskAssessment,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    *,
    nav_trends_by_code: dict[str, dict] | None = None,
    northbound_net_yi: float | None = None,
    facts: dict | None = None,
) -> tuple[list[str], list[FundRecommendation]]:
    weight_denominator = resolve_weight_denominator(request.holdings, request.profile) or 1
    offline_map = _offline_by_holding(
        request,
        weight_denominator,
        market_news,
        nav_trends_by_code=nav_trends_by_code,
        northbound_net_yi=northbound_net_yi,
    )
    settings = get_settings()
    decision_style = request.profile.decision_style
    tactical = decision_style == "tactical"
    aggressive = decision_style == "aggressive"
    short_term = is_short_term_style(decision_style)
    guard_policy = (
        resolve_signal_guard_policy(
            request.holdings,
            lookback_reports=settings.tactical_prompt_tuning_lookback_reports,
            backtest_days=settings.sector_signal_backtest_days,
        )
        if settings.tactical_prompt_tuning_enabled or settings.sector_signal_backtest_enabled
        else {
            "tighten_tactical": False,
            "enforce_reversal_block": True,
            "enforce_pullback_block": True,
            "hints": [],
            "reason": None,
        }
    )
    tuning = guard_policy
    today_signal = has_today_market_signal(market_news, topic_briefs)

    guarded: list[FundRecommendation] = []
    for rec in fund_recs:
        holding = _match_holding(rec, request.holdings)
        offline = None
        if holding is not None:
            offline = offline_map.get(holding.fund_code) or offline_map.get(holding.fund_name)

        normalized = normalize_action_text(rec.action)

        nav_trend = None
        if holding is not None and nav_trends_by_code:
            nav_trend = nav_trends_by_code.get(holding.fund_code)

        reversal_note = None
        if holding is not None and _reversal_signal_block(
            holding,
            nav_trend,
            enforce_reversal=bool(guard_policy.get("enforce_reversal_block", True)),
            enforce_pullback=bool(guard_policy.get("enforce_pullback_block", True)),
        ):
            if _action_bucket(normalized) >= 3 or _action_bucket(rec.action) >= 3:
                if tactical:
                    normalized = "观察"
                    reversal_note = "涨后回吐或盘中冲高回落，战术模式已限制追涨加仓。"
                else:
                    normalized = "暂停追涨"
                    reversal_note = "涨后回吐或盘中冲高回落，已限制追涨加仓（板块短线信号）。"
            elif tactical and tuning.get("tighten_tactical") and _action_bucket(normalized) >= 2:
                normalized = "观察"
                reversal_note = "历史涨后回吐命中率偏低，战术模式已自动收紧：回吐场景优先观察。"

        if offline is not None and not short_term and not reversal_note:
            normalized = conservative_action_text(normalized, offline.action)

        max_bucket = _max_allowed_bucket(
            risk, holding, request, tactical=tactical, aggressive=aggressive
        )
        if _action_bucket(normalized) > max_bucket:
            normalized = _BUCKET_TO_LABEL[_bucket_name(max_bucket)]

        facts_row = _facts_row_for_holding(facts, holding) if holding is not None else None
        sector_opportunity = (facts_row or {}).get("sector_opportunity")
        evidence = (facts_row or {}).get("evidence")

        weak_note = None
        if not reversal_note and _action_bucket(normalized) >= 3:
            weak_reasons = _weak_evidence_reasons(sector_opportunity, evidence)
            if weak_reasons:
                normalized = "观察"
                max_bucket = min(max_bucket, 1)
                weak_note = (
                    f"板块或基金证据不足（{'、'.join(weak_reasons)}），"
                    "已将加仓类动作降为「观察」。"
                )

        if (
            not short_term
            and settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(normalized) >= 3
        ):
            normalized = "暂停追涨"
            max_bucket = min(max_bucket, 2)

        note = reversal_note or weak_note
        if (
            not note
            and not short_term
            and settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(rec.action.strip()) >= 3
            and normalized != rec.action.strip()
        ):
            note = "无当日可引用要闻，已限制激进加仓类动作（更贴盘面、防幻觉）。"
        elif not note and offline is not None and not short_term and normalized != rec.action.strip():
            note = f"已按风控规则将「{rec.action.strip()}」调整为「{normalized}」（对照本地规则：{offline.action}）。"
        elif not note and tactical and normalized != rec.action.strip():
            note = f"战术模式下保留模型动作「{normalized}」（未与离线规则取更保守值）。"
        elif not note and aggressive and normalized != rec.action.strip():
            note = f"激进波段模式保留模型动作「{normalized}」（对照离线规则：{offline.action if offline else '—'}）。"
        elif not note and normalized != rec.action.strip():
            note = f"已规范动作表述为「{normalized}」。"

        copy = rec.model_copy(update={"action": normalized})
        if note:
            copy.points = [note, *copy.points]
        copy.confidence = _normalize_confidence(copy.confidence)
        _backfill_decision_fields(copy, holding, sector_opportunity, evidence)
        _sync_decision_path_with_final_action(copy)
        _humanize_recommendation_text(copy)
        guarded.append(copy)

    portfolio = _guard_portfolio_lines(portfolio_lines, risk)
    if not short_term and settings.news_require_today_for_add and not today_signal:
        hint = "当日无已引用要闻支撑，组合级建议以观察/控风险为主，不宜激进加仓。"
        if not portfolio or hint not in portfolio[0]:
            portfolio = [hint, *portfolio]
    elif aggressive:
        from app.services.investment_presets import take_profit_threshold_percent

        threshold = take_profit_threshold_percent(request.profile)
        hint = (
            f"激进波段模式：跌深分批买、持有收益达 {threshold:.1f}%（含手续费）优先止盈，"
            f"目标持有 {request.profile.hold_days_target} 天内，仍须遵守集中度与浮亏线。"
        )
        if not portfolio or hint not in portfolio[0]:
            portfolio = [hint, *portfolio]
    elif tactical:
        hint = "战术短线模式：建议侧重当日/次日盘面与板块动能，仍须遵守集中度与风险复核线。"
        if tuning.get("tighten_tactical") and tuning.get("reason"):
            hint = str(tuning["reason"])
        if not portfolio or hint not in portfolio[0]:
            portfolio = [hint, *portfolio]
    return portfolio, guarded


def _reversal_signal_block(
    holding: Holding,
    nav_trend: dict | None,
    *,
    enforce_reversal: bool = True,
    enforce_pullback: bool = True,
) -> bool:
    if enforce_reversal:
        momentum = build_sector_momentum_context(holding, nav_trend)
        if momentum and momentum.get("pattern_label") == "two_day_reversal_down":
            return True
    if enforce_pullback:
        intraday = summarize_sector_intraday_for_holding(holding)
        if intraday and intraday.get("pattern_label") == "intraday_pullback":
            return True
    return False


def normalize_action_text(action: str) -> str:
    cleaned = (action or "").strip() or "观察"
    bucket = _action_bucket(cleaned)
    label = _BUCKET_TO_LABEL[_bucket_name(bucket)]
    if bucket == 0 and ("复核" in cleaned or "风控" in cleaned):
        return "风控复核"
    return label


def conservative_action_text(llm_action: str, offline_action: str) -> str:
    llm_bucket = _action_bucket(normalize_action_text(llm_action))
    offline_bucket = _action_bucket(normalize_action_text(offline_action))
    chosen = min(llm_bucket, offline_bucket)
    if chosen == 0 and ("复核" in offline_action or "风控" in offline_action):
        return "风控复核"
    return _BUCKET_TO_LABEL[_bucket_name(chosen)]


def _offline_by_holding(
    request: AnalysisRequest,
    weight_denominator: float,
    market_news: list[NewsItem] | None,
    *,
    nav_trends_by_code: dict[str, dict] | None = None,
    northbound_net_yi: float | None = None,
) -> dict[str, FundRecommendation]:
    nav_trends = nav_trends_by_code or {}
    mapping: dict[str, FundRecommendation] = {}
    for holding in request.holdings:
        weight = holding_weight_percent(holding, request.holdings, request.profile)
        offline = build_offline_fund_recommendation(
            holding,
            weight,
            weight_denominator,
            request.profile,
            market_news=market_news,
            nav_trend=nav_trends.get(holding.fund_code),
            northbound_net_yi=northbound_net_yi,
        )
        mapping[holding.fund_code] = offline
        mapping[holding.fund_name] = offline
    return mapping


def _match_holding(rec: FundRecommendation, holdings: list[Holding]) -> Holding | None:
    for holding in holdings:
        if rec.fund_code != "000000" and holding.fund_code == rec.fund_code:
            return holding
        if holding.fund_name == rec.fund_name:
            return holding
    return None


def _action_bucket(action: str) -> int:
    text = action.strip()
    if any(token in text for token in ("减仓", "复核", "风控", "降仓")):
        return 0
    if any(token in text for token in ("暂停", "勿追涨", "勿追", "观望")):
        return 2
    if any(token in text for token in ("加仓", "定投", "分批")):
        return 3
    return 1


def _bucket_name(bucket: int) -> str:
    for name, value in _ACTION_BUCKET.items():
        if value == bucket:
            return name
    return "watch"


def _max_allowed_bucket(
    risk: RiskAssessment,
    holding,
    request: AnalysisRequest,
    *,
    tactical: bool = False,
    aggressive: bool = False,
) -> int:
    if risk.suggested_action == "risk_review":
        return 2
    if risk.level == "high":
        return 2
    if (
        not tactical
        and not aggressive
        and holding is not None
        and request.profile.avoid_chasing
    ):
        sector = getattr(holding, "sector_return_percent", None)
        if sector is not None and sector > 5:
            return 2
    return 3


def _facts_row_for_holding(facts: dict | None, holding: Holding | None) -> dict | None:
    if not facts or holding is None:
        return None
    for row in facts.get("holdings") or []:
        if isinstance(row, dict) and row.get("fund_code") == holding.fund_code:
            return row
    return None


def _weak_evidence_reasons(sector_opportunity: dict | None, evidence: dict | None) -> list[str]:
    """加仓类动作要求「板块方向」与「基金自身证据」至少有一路站得住，否则视为证据不足。"""
    reasons: list[str] = []
    if sector_opportunity:
        if sector_opportunity.get("opportunity_available") is False:
            reasons.append("持仓板块当前不构成机会")
        confidence = str(sector_opportunity.get("confidence") or "")
        if confidence in {"低", "不足"}:
            reasons.append("板块方向置信偏低")
        pattern = str(sector_opportunity.get("pattern_label") or "")
        if pattern in {"distribution", "weak_outflow"}:
            reasons.append("板块资金流偏弱")
    if evidence:
        composite = evidence.get("composite") or {}
        level = str(composite.get("level") or "")
        if level in {"低", "不足"}:
            reasons.append("量化证据背书弱")
    return _append_unique([], reasons, limit=4)


def _backfill_decision_fields(
    rec: FundRecommendation,
    holding: Holding | None,
    sector_opportunity: dict | None,
    evidence: dict | None,
) -> None:
    if not rec.decision_path:
        rec.decision_path = _build_decision_path(rec, holding, sector_opportunity, evidence)
    if not rec.sector_evidence:
        rec.sector_evidence = _append_unique([], _build_sector_evidence(sector_opportunity), limit=4)
    if not rec.fund_evidence:
        rec.fund_evidence = _append_unique([], _build_fund_evidence(evidence), limit=4)
    if not rec.validation_notes:
        rec.validation_notes = _append_unique(
            [],
            _build_validation_notes(sector_opportunity, evidence),
            limit=4,
        )
    if not rec.risks:
        rec.risks = _append_unique([], _build_default_risks(rec, sector_opportunity), limit=3)


def _build_decision_path(
    rec: FundRecommendation,
    holding: Holding | None,
    sector_opportunity: dict | None,
    evidence: dict | None,
) -> str:
    sector = (holding.sector_name if holding else None) or "该持仓板块"
    if sector_opportunity:
        track = sector_opportunity.get("track") or "unknown"
        confidence = sector_opportunity.get("confidence") or "中"
        sector_clause = f"先看持仓板块方向：{sector}（{_track_label(track)}，置信{confidence}）"
    else:
        sector_clause = f"先看持仓板块方向：{sector}（暂无独立方向信号）"
    if evidence:
        level = (evidence.get("composite") or {}).get("level") or "不足"
        fund_clause = f"再看该基金自身量化证据（综合置信{level}）"
    else:
        fund_clause = "再看该基金自身持仓与风控数据"
    return f"{sector_clause}，{fund_clause}，动作定为{rec.action}。"


def _build_sector_evidence(sector_opportunity: dict | None) -> list[str]:
    if not sector_opportunity:
        return []
    evidence: list[str] = []
    track = sector_opportunity.get("track")
    confidence = sector_opportunity.get("confidence")
    if track:
        text = _track_label(track)
        if confidence:
            text += f"，置信度{confidence}"
        evidence.append(text)
    today_flow = sector_opportunity.get("today_main_force_net_yi")
    five_day_flow = sector_opportunity.get("cumulative_5d_net_yi")
    if today_flow is not None or five_day_flow is not None:
        parts = []
        if today_flow is not None:
            parts.append(f"今日主力净流入 {_fmt_num(today_flow)} 亿")
        if five_day_flow is not None:
            parts.append(f"5日主力净流入 {_fmt_num(five_day_flow)} 亿")
        evidence.append("，".join(parts))
    pattern = sector_opportunity.get("pattern_label")
    if pattern:
        evidence.append(f"资金/价格信号：{_pattern_label(str(pattern))}")
    if sector_opportunity.get("opportunity_available") is False:
        evidence.append("当前不构成加仓机会，仅供方向参考")
    evidence.extend(
        str(item) for item in sector_opportunity.get("evidence") or [] if str(item).strip()
    )
    return evidence


def _build_fund_evidence(evidence: dict | None) -> list[str]:
    if not evidence:
        return []
    result: list[str] = []
    composite = evidence.get("composite") or {}
    level = composite.get("level")
    if level:
        result.append(f"三路量化证据综合置信：{level}")
    for component in evidence.get("components") or []:
        basis = component.get("basis")
        if basis:
            result.append(str(basis))
    return result


def _build_validation_notes(sector_opportunity: dict | None, evidence: dict | None) -> list[str]:
    notes: list[str] = []
    if sector_opportunity:
        notes.extend(
            str(item) for item in sector_opportunity.get("penalties") or [] if str(item).strip()
        )
    if evidence:
        level = (evidence.get("composite") or {}).get("level")
        if level in {"低", "不足"}:
            notes.append("量化证据样本有限，结论须保守表述")
    if not sector_opportunity:
        notes.append("暂无独立板块方向数据，方向判断仅供参考")
    return notes


def _build_default_risks(rec: FundRecommendation, sector_opportunity: dict | None) -> list[str]:
    if "加仓" in rec.action or "分批" in rec.action or "定投" in rec.action:
        if sector_opportunity and sector_opportunity.get("opportunity_available") is False:
            return ["板块当前不构成机会，加仓后仍可能面临回调"]
        return ["板块或市场波动可能导致净值短期回撤"]
    if "减仓" in rec.action or "复核" in rec.action:
        return ["减仓后若板块反弹可能错过修复行情"]
    return ["市场波动可能影响短期净值表现"]


def _sync_decision_path_with_final_action(rec: FundRecommendation) -> None:
    if not rec.decision_path:
        return
    action = rec.action
    if action in rec.decision_path and not _contains_conflicting_action(rec.decision_path, action):
        return
    if "动作" not in rec.decision_path and not _contains_conflicting_action(rec.decision_path, action):
        return
    text = _strip_conflicting_action_clause(rec.decision_path, action)
    text = text.rstrip("。；;，, ")
    rec.decision_path = f"{text}。系统校验后最终动作调整为{action}。"


def _contains_conflicting_action(text: str, final_action: str) -> bool:
    for candidate in _BUCKET_TO_LABEL.values():
        if candidate != final_action and candidate in text:
            return True
    return False


def _strip_conflicting_action_clause(text: str, final_action: str) -> str:
    result = text
    for candidate in _BUCKET_TO_LABEL.values():
        if candidate == final_action:
            continue
        result = re.sub(rf"，?最后决定[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
        result = re.sub(rf"，?动作[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
    return result


def _humanize_recommendation_text(rec: FundRecommendation) -> None:
    rec.decision_path = _humanize_evidence_text(
        rec.decision_path, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS
    )
    rec.amount_note = (
        _humanize_evidence_text(rec.amount_note, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        if rec.amount_note
        else rec.amount_note
    )
    rec.sector_evidence = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.sector_evidence
    ]
    rec.fund_evidence = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.fund_evidence
    ]
    rec.validation_notes = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.validation_notes
    ]
    rec.points = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.points
    ]
    rec.risks = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.risks
    ]


def _guard_portfolio_lines(lines: list[str], risk: RiskAssessment) -> list[str]:
    if risk.suggested_action != "risk_review":
        return lines

    mandatory = "组合已触发风险复核线，今日以控风险为先，不建议新增加仓。"
    if lines and mandatory in lines[0]:
        return lines
    return [mandatory, *lines]
