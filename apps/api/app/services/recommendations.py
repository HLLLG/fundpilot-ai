from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    TopicBrief,
)
from app.services.holding_metrics import compute_estimated_daily_return_percent
from app.services.risk import holding_weight_percent, resolve_weight_denominator

_BULLISH_HINTS = ("涨", "拉升", "利好", "突破", "创新高", "增持", "流入", "涨停", "走强", "反弹")
_BEARISH_HINTS = (
    "跌",
    "回落",
    "利空",
    "减持",
    "下调",
    "亏损",
    "暴雷",
    "调查",
    "制裁",
    "走弱",
    "跳水",
    "爆炸",
    "事故",
)

_FUND_TAG_RE = re.compile(r"^\[(\d{6})\s*[·｜|]\s*([^\]]+)\]\s*(.*)$", re.DOTALL)
_LEGACY_PIPE_RE = re.compile(r"^(.+?)｜决策：([^｜]+)｜")


def suggest_trade_amount(
    holding: Holding,
    weight_percent: float,
    weight_denominator: float,
    profile: InvestorProfile,
    action: str,
) -> tuple[float | None, str | None]:
    limit = profile.concentration_limit_percent
    if weight_denominator <= 0:
        return None, None

    if weight_percent > limit:
        target_value = weight_denominator * limit / 100
        reduce_yuan = holding.holding_amount - target_value
        if reduce_yuan >= 100:
            rounded = round(reduce_yuan, 0)
            return rounded, (
                f"减仓约 {rounded:,.0f} 元，可将占比从 {weight_percent:.1f}% "
                f"降至约 {limit:.0f}% 以内（示意金额，请结合到账与费率调整）"
            )

    if "减仓" in action or "复核" in action:
        partial = round(holding.holding_amount * 0.15, 0)
        if partial >= 100:
            return partial, (
                f"示意减仓约 {partial:,.0f} 元（约为当前持仓 15%，"
                "请结合浮亏、流动性与费率自行调整）"
            )
        return None, None

    if "加仓" in action or "定投" in action or "分批" in action:
        target_value = weight_denominator * limit / 100
        room = max(target_value - holding.holding_amount, 0)
        if room < 100:
            return None, None
        add_yuan = min(room * 0.15, 2000, room)
        if add_yuan >= 100:
            rounded = round(add_yuan, 0)
            return rounded, (
                f"分批加仓约 {rounded:,.0f} 元（占剩余仓位空间的示意额度，勿一次性打满）"
            )

    return None, None


def attach_sector_news(
    recommendation: FundRecommendation,
    holding: Holding,
    market_news: list[NewsItem],
) -> FundRecommendation:
    bullish, bearish = classify_sector_news(holding, market_news)
    recommendation.news_bullish = bullish
    recommendation.news_bearish = bearish
    return recommendation


def attach_news_from_briefs(
    recommendation: FundRecommendation,
    holding: Holding,
    topic_briefs: list[TopicBrief],
    market_news: list[NewsItem],
) -> FundRecommendation:
    brief = _find_brief_for_holding(holding, topic_briefs)
    if brief is None:
        return attach_sector_news(recommendation, holding, market_news)

    bullish: list[str] = []
    bearish: list[str] = []
    for point in brief.points:
        label = point.headline
        for title in point.source_titles:
            dated = f"{title}（{brief.topic}）"
            if point.sentiment == "bearish":
                if dated not in bearish:
                    bearish.append(dated)
            elif point.sentiment == "bullish":
                if dated not in bullish:
                    bullish.append(dated)
            elif point.is_today and dated not in bullish:
                bullish.append(f"{dated}（中性/待核实）")

    if bullish or bearish:
        recommendation.news_bullish = bullish[:3]
        recommendation.news_bearish = bearish[:3]
        return recommendation

    return attach_sector_news(recommendation, holding, market_news)


def _find_brief_for_holding(holding: Holding, topic_briefs: list[TopicBrief]) -> TopicBrief | None:
    sector = holding.sector_name or ""
    for brief in topic_briefs:
        topic = brief.topic
        if topic == holding.fund_code:
            return brief
        if sector and (topic in sector or sector in topic):
            return brief
        for token in ("人工智能", "电网设备", "半导体", "国防军工", "商业航天"):
            if token in holding.fund_name and token in topic:
                return brief
    return None


def classify_sector_news(
    holding: Holding,
    market_news: list[NewsItem],
) -> tuple[list[str], list[str]]:
    sector = holding.sector_name or ""
    bullish: list[str] = []
    bearish: list[str] = []

    for item in market_news:
        if not _news_matches_holding(item, holding, sector):
            continue
        label = _format_news_label(item)
        text = f"{item.title}（{item.published_at or '时间未知'}）"
        if _contains_any(item.title + (item.snippet or ""), _BEARISH_HINTS):
            if label not in bearish:
                bearish.append(text)
        elif _contains_any(item.title + (item.snippet or ""), _BULLISH_HINTS):
            if label not in bullish:
                bullish.append(text)
        elif item.is_today:
            if label not in bullish:
                bullish.append(f"{text}（中性/待核实）")

    return bullish[:3], bearish[:3]


def _news_matches_holding(item: NewsItem, holding: Holding, sector: str) -> bool:
    topics = dict.fromkeys([item.topic, *item.related_topics])
    for topic in topics:
        if topic == holding.fund_code:
            return True
        if sector and (topic in sector or sector in topic):
            return True
        for token in ("人工智能", "电网设备", "半导体", "国防军工", "商业航天"):
            if token in holding.fund_name and token in topic:
                return True
    return False


def _format_news_label(item: NewsItem) -> str:
    return item.title


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def build_offline_fund_recommendation(
    holding: Holding,
    weight_percent: float,
    weight_denominator: float,
    profile: InvestorProfile,
    market_news: list[NewsItem] | None = None,
    *,
    nav_trend: dict | None = None,
) -> FundRecommendation:
    if profile.decision_style == "tactical":
        from app.services.tactical_recommendations import (
            build_tactical_offline_fund_recommendation,
        )

        return build_tactical_offline_fund_recommendation(
            holding,
            weight_percent,
            weight_denominator,
            profile,
            market_news,
            nav_trend=nav_trend,
        )

    if profile.decision_style == "aggressive":
        from app.services.aggressive_swing_recommendations import (
            build_aggressive_swing_offline_fund_recommendation,
        )

        return build_aggressive_swing_offline_fund_recommendation(
            holding,
            weight_percent,
            weight_denominator,
            profile,
            market_news,
            nav_trend=nav_trend,
        )

    action = "观察"
    points: list[str] = []

    if weight_percent > profile.concentration_limit_percent:
        action = "减仓评估"
        points.append(
            f"仓位 {weight_percent:.1f}% 超过集中度上限 {profile.concentration_limit_percent:.0f}%，优先降仓。"
        )
    elif holding.sector_return_percent is not None and holding.sector_return_percent > 5:
        action = "暂停追涨"
        points.append(
            f"关联板块当日 +{holding.sector_return_percent:.2f}%，避免追高，等待回落再考虑分批。"
        )
    estimated_daily = compute_estimated_daily_return_percent(holding)
    if (
        estimated_daily is not None
        and holding.daily_return_percent is None
        and estimated_daily > 5
        and action == "观察"
    ):
        action = "暂停追涨"
        points.append(
            f"估算当日涨跌约 +{estimated_daily:.2f}%（板块+昨日持有收益率），避免追涨。"
        )
    elif (holding.holding_return_percent or holding.return_percent) < -5 and profile.prefer_dca:
        action = "分批加仓"
        points.append("持有收益偏弱且风格偏定投，仅考虑小额分批，不一次性补仓。")
    else:
        points.append("未触发硬性风控，维持观察，确认板块与净值后再动。")

    sector = holding.sector_name or "未知板块"
    daily = "-" if holding.daily_profit is None else f"{holding.daily_profit:.2f}"
    if holding.daily_return_percent is not None:
        daily_return = f"{holding.daily_return_percent:.2f}%"
    elif estimated_daily is not None:
        daily_return = f"≈{estimated_daily:.2f}%（板块+持有收益率估算）"
    else:
        daily_return = "-"
    holding_return = (
        "-"
        if holding.holding_return_percent is None
        else f"{holding.holding_return_percent:.2f}%"
    )
    sector_change = (
        "-"
        if holding.sector_return_percent is None
        else f"{holding.sector_return_percent:.2f}%"
    )
    points.append(
        f"当日收益 {daily} / {daily_return}；持有收益率 {holding_return}；"
        f"板块 {sector} {sector_change}。"
    )
    if holding.fund_code == "000000":
        points.append("基金代码未补全，补全后可核对净值与公告。")

    amount_yuan, amount_note = suggest_trade_amount(
        holding, weight_percent, weight_denominator, profile, action
    )

    rec = FundRecommendation(
        fund_code=holding.fund_code,
        fund_name=holding.fund_name,
        action=action,
        amount_yuan=amount_yuan,
        amount_note=amount_note,
        points=points,
    )
    return attach_sector_news(rec, holding, market_news or [])


def build_offline_fund_recommendations(
    request: AnalysisRequest,
    market_news: list[NewsItem] | None = None,
    *,
    nav_trends_by_code: dict[str, dict] | None = None,
) -> list[FundRecommendation]:
    """Build the shared holdings-complete local fallback without applying guards."""
    denominator = resolve_weight_denominator(request.holdings, request.profile) or 1
    nav_trends = nav_trends_by_code or {}
    fallback = [
        build_offline_fund_recommendation(
            holding,
            holding_weight_percent(holding, request.holdings, request.profile),
            denominator,
            request.profile,
            market_news=market_news or [],
            nav_trend=nav_trends.get(holding.fund_code),
        )
        for holding in request.holdings
    ]
    return canonicalize_fund_recommendations(
        fallback,
        request.holdings,
        fallback_recommendations=fallback,
    )


def parse_fund_recommendations_raw(
    raw: object,
    *,
    merge_items: bool = True,
) -> list[FundRecommendation]:
    if not isinstance(raw, list):
        return []

    items: list[FundRecommendation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        fund_code = str(entry.get("fund_code", "")).strip()
        fund_name = str(entry.get("fund_name", "")).strip()
        from app.services.recommendation_guard import normalize_action_text

        action = normalize_action_text(str(entry.get("action", "观察")))
        if not fund_code and not fund_name:
            continue

        points_raw = entry.get("points")
        points: list[str] = []
        if isinstance(points_raw, list):
            points = [str(point).strip() for point in points_raw if str(point).strip()]

        amount_yuan = _coerce_amount(entry.get("amount_yuan"))
        amount_note = entry.get("amount_note")
        if amount_note is not None:
            amount_note = str(amount_note).strip() or None

        confidence = str(entry.get("confidence") or "中").strip() or "中"
        hold_horizon = str(entry.get("hold_horizon") or "").strip()
        decision_path = str(entry.get("decision_path") or "").strip()
        suggested_position_change_percent = _coerce_number(
            entry.get("suggested_position_change_percent")
        )
        suggested_position_change_basis = str(
            entry.get("suggested_position_change_basis") or ""
        ).strip()

        items.append(
            FundRecommendation(
                fund_code=fund_code or "000000",
                fund_name=fund_name or fund_code,
                action=action,
                amount_yuan=amount_yuan,
                amount_note=amount_note,
                news_bullish=_string_list(entry.get("news_bullish")),
                news_bearish=_string_list(entry.get("news_bearish")),
                points=points,
                confidence=confidence,
                hold_horizon=hold_horizon,
                risks=_string_list(entry.get("risks")),
                decision_path=decision_path,
                sector_evidence=_string_list(entry.get("sector_evidence")),
                fund_evidence=_string_list(entry.get("fund_evidence")),
                validation_notes=_string_list(entry.get("validation_notes")),
                suggested_position_change_percent=suggested_position_change_percent,
                suggested_position_change_basis=suggested_position_change_basis,
            )
        )
    return merge_fund_recommendations(items) if merge_items else items


def canonicalize_fund_recommendations(
    items: Sequence[FundRecommendation],
    holdings: Sequence[Holding],
    *,
    fallback_recommendations: Sequence[FundRecommendation] | None = None,
) -> list[FundRecommendation]:
    """Close model output over the server-owned holdings list.

    The function is deliberately pure and idempotent: it never mutates its inputs,
    emits exactly one item per holding in holding order, drops out-of-portfolio
    suggestions, and fails closed when duplicate suggestions disagree.
    """
    holding_list = list(holdings)
    if not holding_list:
        return []

    assigned = _assign_recommendations_to_holdings(items, holding_list)
    fallback_assigned = _assign_recommendations_to_holdings(
        fallback_recommendations or [], holding_list
    )

    result: list[FundRecommendation] = []
    for index, holding in enumerate(holding_list):
        candidates = assigned[index]
        if candidates:
            recommendation = _merge_canonical_candidates(candidates)
        elif fallback_assigned[index]:
            recommendation = _merge_canonical_candidates(fallback_assigned[index])
            fallback_note = "模型未返回该持仓的有效建议，系统已采用本地保守规则补齐。"
            if fallback_note not in recommendation.validation_notes:
                recommendation.validation_notes.append(fallback_note)
        else:
            recommendation = _missing_holding_recommendation(holding)

        recommendation.fund_code = holding.fund_code
        recommendation.fund_name = holding.fund_name
        result.append(recommendation)
    return result


def _assign_recommendations_to_holdings(
    items: Sequence[FundRecommendation],
    holdings: list[Holding],
) -> list[list[FundRecommendation]]:
    grouped: list[list[tuple[int, FundRecommendation]]] = [[] for _ in holdings]
    by_code: dict[str, list[int]] = defaultdict(list)
    by_name: dict[str, list[int]] = defaultdict(list)
    for index, holding in enumerate(holdings):
        code = _valid_identity_code(holding.fund_code)
        if code is not None:
            by_code[code].append(index)
        by_name[_normalize_fund_name(holding.fund_name)].append(index)
    placeholder_holding_indices = [
        index
        for index, holding in enumerate(holdings)
        if _valid_identity_code(holding.fund_code) is None
    ]

    indexed_items = list(enumerate(items))
    valid_code_items: list[tuple[int, FundRecommendation]] = []
    fallback_identity_items: list[tuple[int, FundRecommendation]] = []
    for indexed in indexed_items:
        if _valid_identity_code(indexed[1].fund_code) is not None:
            valid_code_items.append(indexed)
        else:
            fallback_identity_items.append(indexed)

    # A real six-digit code is authoritative. Process it first so weaker name
    # matching cannot claim an ambiguous duplicate-code holding ahead of it.
    for input_index, item in valid_code_items:
        code = _valid_identity_code(item.fund_code)
        candidates = by_code.get(code or "", [])
        if not candidates:
            # A valid code outside the request is an outsider, even when its name
            # happens to imitate one of the user's holdings.
            continue
        target = _choose_stable_holding_index(
            candidates,
            grouped,
            preferred_name=_normalize_fund_name(item.fund_name),
            holdings=holdings,
        )
        grouped[target].append((input_index, item.model_copy(deep=True)))

    for input_index, item in fallback_identity_items:
        normalized_name = _normalize_fund_name(item.fund_name)
        candidates = by_name.get(normalized_name, [])
        if not candidates:
            # Unknown/placeholder identities may be aligned by stable request
            # order. A concrete but foreign name is discarded instead of being
            # allowed to smuggle an action into the portfolio.
            if not _is_placeholder_fund_name(normalized_name):
                continue
            # A placeholder model identity may only consume an authoritative
            # placeholder holding. Letting it fall through to every holding can
            # smuggle an action/amount onto a valid unique fund code.
            candidates = placeholder_holding_indices
            if not candidates:
                continue
        target = _choose_stable_holding_index(
            candidates,
            grouped,
            preferred_name=normalized_name,
            holdings=holdings,
        )
        grouped[target].append((input_index, item.model_copy(deep=True)))

    return [
        [item for _, item in sorted(group, key=lambda pair: pair[0])]
        for group in grouped
    ]


def _choose_stable_holding_index(
    candidates: Sequence[int],
    grouped: Sequence[list[tuple[int, FundRecommendation]]],
    *,
    preferred_name: str,
    holdings: Sequence[Holding],
) -> int:
    name_matches = [
        index
        for index in candidates
        if _normalize_fund_name(holdings[index].fund_name) == preferred_name
    ]
    ordered = name_matches or list(candidates)
    for index in ordered:
        if not grouped[index]:
            return index
    return ordered[0]


def _merge_canonical_candidates(
    candidates: Sequence[FundRecommendation],
) -> FundRecommendation:
    from app.services.recommendation_guard import normalize_action_text

    copies = [item.model_copy(deep=True) for item in candidates]
    normalized_actions = [normalize_action_text(item.action) for item in copies]
    unique_actions = list(dict.fromkeys(normalized_actions))
    result = copies[0]
    result.action = unique_actions[0]

    for field in (
        "news_bullish",
        "news_bearish",
        "points",
        "risks",
        "sector_evidence",
        "fund_evidence",
        "validation_notes",
    ):
        setattr(result, field, _stable_unique_strings(copies, field))

    result.confidence = _least_confident(item.confidence for item in copies)
    result.hold_horizon = _first_nonempty(item.hold_horizon for item in copies)
    result.decision_path = _first_nonempty(item.decision_path for item in copies)

    if len(unique_actions) > 1:
        result.action = (
            "风控复核"
            if any(_is_risk_or_reduction_action(action) for action in unique_actions)
            else "观察"
        )
        result.amount_yuan = None
        result.amount_note = None
        result.suggested_position_change_percent = None
        result.suggested_position_change_basis = ""
        result.confidence = "低"
        conflict_note = (
            f"检测到同一持仓的重复建议动作冲突（{' / '.join(unique_actions)}），"
            f"系统已清除可执行金额并降为{result.action}。"
        )
        if conflict_note not in result.validation_notes:
            result.validation_notes.append(conflict_note)
        return result

    if _has_action_conflict_note(result):
        result.action = (
            "风控复核" if _is_risk_or_reduction_action(result.action) else "观察"
        )
        result.amount_yuan = None
        result.amount_note = None
        result.suggested_position_change_percent = None
        result.suggested_position_change_basis = ""
        result.confidence = "低"
        return result

    if _has_amount_conflict_note(result):
        result.amount_yuan = None
        result.amount_note = None
        result.confidence = "低"
    else:
        amount_values = _unique_non_null(item.amount_yuan for item in copies)
        if len(amount_values) <= 1:
            result.amount_yuan = amount_values[0] if amount_values else None
            result.amount_note = (
                _first_nonempty(item.amount_note for item in copies) or None
            )
        else:
            result.amount_yuan = None
            result.amount_note = None
            result.confidence = "低"
            note = "同一动作的重复建议金额不一致，系统已清除金额并要求人工复核。"
            if note not in result.validation_notes:
                result.validation_notes.append(note)

    if _has_position_conflict_note(result):
        result.suggested_position_change_percent = None
        result.suggested_position_change_basis = ""
        result.confidence = "低"
    else:
        position_values = _unique_non_null(
            item.suggested_position_change_percent for item in copies
        )
        if len(position_values) <= 1:
            result.suggested_position_change_percent = (
                position_values[0] if position_values else None
            )
            result.suggested_position_change_basis = _first_nonempty(
                item.suggested_position_change_basis for item in copies
            )
        else:
            result.suggested_position_change_percent = None
            result.suggested_position_change_basis = ""
            result.confidence = "低"
            note = "同一动作的重复建议仓位变化不一致，系统已清除仓位比例并要求人工复核。"
            if note not in result.validation_notes:
                result.validation_notes.append(note)
    return result


def _missing_holding_recommendation(holding: Holding) -> FundRecommendation:
    note = "模型未返回该持仓的有效建议，系统已按保守规则补为观察。"
    return FundRecommendation(
        fund_code=holding.fund_code,
        fund_name=holding.fund_name,
        action="观察",
        confidence="低",
        points=[note],
        risks=["建议缺失，操作前需人工复核持仓与最新数据。"],
        validation_notes=[note],
    )


def _valid_identity_code(value: object) -> str | None:
    code = str(value or "").strip()
    if code != "000000" and re.fullmatch(r"[0-9]{6}", code):
        return code
    return None


def _normalize_fund_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _is_placeholder_fund_name(value: str) -> bool:
    return value in {"", "000000", "未知", "未知基金", "基金", "unknown"}


def _stable_unique_strings(
    items: Sequence[FundRecommendation], field: str
) -> list[str]:
    result: list[str] = []
    for item in items:
        for value in getattr(item, field):
            if value and value not in result:
                result.append(value)
    return result


def _least_confident(values: Iterable[object]) -> str:
    ordered = {"低": 0, "中": 1, "高": 2}
    cleaned = [str(value or "中").strip() or "中" for value in values]
    return min(cleaned, key=lambda value: ordered.get(value, 0), default="中")


def _first_nonempty(values: Iterable[object]) -> str:
    return next((str(value).strip() for value in values if str(value or "").strip()), "")


def _unique_non_null(values: Iterable[float | None]) -> list[float]:
    result: list[float] = []
    for value in values:
        if value is not None and value not in result:
            result.append(value)
    return result


def _is_risk_or_reduction_action(action: str) -> bool:
    return any(token in action for token in ("减仓", "清仓", "风控", "复核"))


def _has_action_conflict_note(item: FundRecommendation) -> bool:
    return any("重复建议动作冲突" in note for note in item.validation_notes)


def _has_amount_conflict_note(item: FundRecommendation) -> bool:
    return any("重复建议金额不一致" in note for note in item.validation_notes)


def _has_position_conflict_note(item: FundRecommendation) -> bool:
    return any("重复建议仓位变化不一致" in note for note in item.validation_notes)


def _has_executable_conflict_note(item: FundRecommendation) -> bool:
    return (
        _has_action_conflict_note(item)
        or _has_amount_conflict_note(item)
        or _has_position_conflict_note(item)
    )


def merge_fund_recommendations(items: list[FundRecommendation]) -> list[FundRecommendation]:
    merged: dict[str, FundRecommendation] = {}
    order: list[str] = []

    for item in items:
        key = item.fund_code if item.fund_code != "000000" else item.fund_name
        if key not in merged:
            merged[key] = item.model_copy(deep=True)
            order.append(key)
            continue

        existing = merged[key]
        if _action_priority(item.action) > _action_priority(existing.action):
            existing.action = item.action
        if item.amount_yuan is not None and existing.amount_yuan is None:
            existing.amount_yuan = item.amount_yuan
        if item.amount_note and not existing.amount_note:
            existing.amount_note = item.amount_note
        for point in item.points:
            if point not in existing.points:
                existing.points.append(point)
        for headline in item.news_bullish:
            if headline not in existing.news_bullish:
                existing.news_bullish.append(headline)
        for headline in item.news_bearish:
            if headline not in existing.news_bearish:
                existing.news_bearish.append(headline)
        if item.confidence and existing.confidence == "中" and item.confidence != "中":
            existing.confidence = item.confidence
        if item.hold_horizon and not existing.hold_horizon:
            existing.hold_horizon = item.hold_horizon
        if item.decision_path and not existing.decision_path:
            existing.decision_path = item.decision_path
        for risk in item.risks:
            if risk not in existing.risks:
                existing.risks.append(risk)
        for evidence in item.sector_evidence:
            if evidence not in existing.sector_evidence:
                existing.sector_evidence.append(evidence)
        for evidence in item.fund_evidence:
            if evidence not in existing.fund_evidence:
                existing.fund_evidence.append(evidence)
        for note in item.validation_notes:
            if note not in existing.validation_notes:
                existing.validation_notes.append(note)

    return [merged[key] for key in order]


def group_strings_to_fund_recommendations(
    lines: list[str],
    holdings: list[Holding],
) -> list[FundRecommendation]:
    by_code = {holding.fund_code: holding for holding in holdings if holding.fund_code != "000000"}
    by_name = {holding.fund_name: holding for holding in holdings}
    parsed: list[FundRecommendation] = []

    for line in lines:
        line = line.strip()
        if not line or _is_portfolio_line(line):
            continue

        match = _FUND_TAG_RE.match(line)
        if match:
            code, action, rest = match.group(1), match.group(2).strip(), match.group(3).strip()
            holding = by_code.get(code)
            name = holding.fund_name if holding else code
            point = rest or action
            action_label = action
        else:
            pipe = _LEGACY_PIPE_RE.match(line)
            if pipe:
                name, action_label, _ = pipe.group(1), pipe.group(2).strip(), line
                holding = by_name.get(name.strip())
                code = holding.fund_code if holding else "000000"
                point = line
            else:
                code_match = re.search(r"\b(\d{6})\b", line)
                if not code_match:
                    continue
                code = code_match.group(1)
                holding = by_code.get(code)
                name = holding.fund_name if holding else code
                action_label = "观察"
                point = line

        parsed.append(
            FundRecommendation(
                fund_code=code,
                fund_name=name,
                action=action_label,
                points=[point] if point else [],
            )
        )

    # Keep duplicates intact. The holdings-aware canonicalizer at the shared
    # report outlet must see disagreements so it can fail closed instead of
    # selecting the most aggressive legacy line.
    return parsed


def enrich_fund_recommendations(
    items: list[FundRecommendation],
    request: AnalysisRequest,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    *,
    merge_items: bool = True,
) -> list[FundRecommendation]:
    weight_denominator = resolve_weight_denominator(request.holdings, request.profile) or 1
    holding_by_code = {h.fund_code: h for h in request.holdings}
    holding_by_name = {h.fund_name: h for h in request.holdings}

    enriched: list[FundRecommendation] = []
    for index, item in enumerate(items):
        holding = None
        if index < len(request.holdings):
            aligned = request.holdings[index]
            if (
                item.fund_code == aligned.fund_code
                and item.fund_name == aligned.fund_name
            ):
                holding = aligned
        if holding is None:
            holding = holding_by_code.get(item.fund_code) or holding_by_name.get(
                item.fund_name
            )
        copy = item.model_copy(deep=True)
        if holding:
            copy.fund_code = holding.fund_code
            copy.fund_name = holding.fund_name
            weight = holding_weight_percent(holding, request.holdings, request.profile)
            if copy.amount_yuan is None and not _has_executable_conflict_note(copy):
                amount_yuan, amount_note = suggest_trade_amount(
                    holding, weight, weight_denominator, request.profile, copy.action
                )
                if copy.amount_yuan is None and amount_yuan is not None:
                    copy.amount_yuan = amount_yuan
                if copy.amount_note is None and amount_note is not None:
                    copy.amount_note = amount_note
            if market_news and not copy.news_bullish and not copy.news_bearish:
                if topic_briefs:
                    copy = attach_news_from_briefs(
                        copy, holding, topic_briefs, market_news
                    )
                else:
                    copy = attach_sector_news(copy, holding, market_news)
        enriched.append(copy)
    return merge_fund_recommendations(enriched) if merge_items else enriched


def portfolio_recommendation_lines(lines: list[str], holdings: list[Holding]) -> list[str]:
    return [line for line in lines if _is_portfolio_line(line)]


def _is_portfolio_line(line: str) -> bool:
    if _FUND_TAG_RE.match(line.strip()):
        return False
    if _LEGACY_PIPE_RE.match(line.strip()):
        return False
    if re.search(r"\b\d{6}\b", line) and ("决策" in line or "·" in line or "｜" in line):
        return False
    return True


def _action_priority(action: str) -> int:
    if "减仓" in action:
        return 4
    if "暂停" in action or "勿追涨" in action:
        return 3
    if "加仓" in action or "定投" in action or "分批" in action:
        return 2
    return 1


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_amount(value: Any) -> float | None:
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return round(amount, 0) if amount >= 0 else None


def _coerce_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
