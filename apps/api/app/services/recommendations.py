from __future__ import annotations

import re
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
    topic = item.topic
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
    northbound_net_yi: float | None = None,
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
            northbound_net_yi=northbound_net_yi,
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
            northbound_net_yi=northbound_net_yi,
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


def parse_fund_recommendations_raw(raw: object) -> list[FundRecommendation]:
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
            )
        )
    return merge_fund_recommendations(items)


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

    return [merged[key] for key in order]


def group_strings_to_fund_recommendations(
    lines: list[str],
    holdings: list[Holding],
) -> list[FundRecommendation]:
    by_code = {holding.fund_code: holding for holding in holdings if holding.fund_code != "000000"}
    by_name = {holding.fund_name: holding for holding in holdings}
    grouped: dict[str, FundRecommendation] = {}
    order: list[str] = []

    for line in lines:
        line = line.strip()
        if not line or _is_portfolio_line(line):
            continue

        match = _FUND_TAG_RE.match(line)
        if match:
            code, action, rest = match.group(1), match.group(2).strip(), match.group(3).strip()
            holding = by_code.get(code)
            name = holding.fund_name if holding else code
            key = code
            point = rest or action
            action_label = action
        else:
            pipe = _LEGACY_PIPE_RE.match(line)
            if pipe:
                name, action_label, _ = pipe.group(1), pipe.group(2).strip(), line
                holding = by_name.get(name.strip())
                code = holding.fund_code if holding else "000000"
                key = code if code != "000000" else name.strip()
                point = line
            else:
                code_match = re.search(r"\b(\d{6})\b", line)
                if not code_match:
                    continue
                code = code_match.group(1)
                holding = by_code.get(code)
                name = holding.fund_name if holding else code
                key = code
                action_label = "观察"
                point = line

        if key not in grouped:
            grouped[key] = FundRecommendation(
                fund_code=code,
                fund_name=name,
                action=action_label,
                points=[point] if point else [],
            )
            order.append(key)
        else:
            existing = grouped[key]
            if _action_priority(action_label) > _action_priority(existing.action):
                existing.action = action_label
            if point and point not in existing.points:
                existing.points.append(point)

    return [grouped[key] for key in order]


def enrich_fund_recommendations(
    items: list[FundRecommendation],
    request: AnalysisRequest,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
) -> list[FundRecommendation]:
    weight_denominator = resolve_weight_denominator(request.holdings, request.profile) or 1
    holding_by_code = {h.fund_code: h for h in request.holdings}
    holding_by_name = {h.fund_name: h for h in request.holdings}

    enriched: list[FundRecommendation] = []
    for item in items:
        holding = holding_by_code.get(item.fund_code) or holding_by_name.get(item.fund_name)
        copy = item.model_copy(deep=True)
        if holding:
            copy.fund_code = holding.fund_code
            copy.fund_name = holding.fund_name
            weight = holding_weight_percent(holding, request.holdings, request.profile)
            if copy.amount_yuan is None:
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
    return merge_fund_recommendations(enriched)


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
