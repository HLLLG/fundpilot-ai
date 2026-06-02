from __future__ import annotations

from app.config import get_settings
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.services.market_signal import has_today_market_signal
from app.services.recommendations import build_offline_fund_recommendation

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


def apply_recommendation_guards(
    fund_recs: list[FundRecommendation],
    portfolio_lines: list[str],
    request: AnalysisRequest,
    risk: RiskAssessment,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
) -> tuple[list[str], list[FundRecommendation]]:
    total_amount = sum(holding.holding_amount for holding in request.holdings) or 1
    offline_map = _offline_by_holding(request, total_amount, market_news)
    settings = get_settings()
    today_signal = has_today_market_signal(market_news, topic_briefs)

    guarded: list[FundRecommendation] = []
    for rec in fund_recs:
        holding = _match_holding(rec, request.holdings)
        offline = None
        if holding is not None:
            offline = offline_map.get(holding.fund_code) or offline_map.get(holding.fund_name)

        normalized = normalize_action_text(rec.action)
        if offline is not None:
            normalized = conservative_action_text(normalized, offline.action)

        max_bucket = _max_allowed_bucket(risk, holding, request)
        if _action_bucket(normalized) > max_bucket:
            normalized = _BUCKET_TO_LABEL[_bucket_name(max_bucket)]

        if (
            settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(normalized) >= 3
        ):
            normalized = "暂停追涨"
            max_bucket = min(max_bucket, 2)

        note = None
        if (
            settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(rec.action.strip()) >= 3
            and normalized != rec.action.strip()
        ):
            note = "无当日可引用要闻，已限制激进加仓类动作（更贴盘面、防幻觉）。"
        elif offline is not None and normalized != rec.action.strip():
            note = f"已按风控规则将「{rec.action.strip()}」调整为「{normalized}」（对照本地规则：{offline.action}）。"
        elif normalized != rec.action.strip():
            note = f"已规范动作表述为「{normalized}」。"

        copy = rec.model_copy(update={"action": normalized})
        if note:
            copy.points = [note, *copy.points]
        guarded.append(copy)

    portfolio = _guard_portfolio_lines(portfolio_lines, risk)
    if settings.news_require_today_for_add and not today_signal:
        hint = "当日无已引用要闻支撑，组合级建议以观察/控风险为主，不宜激进加仓。"
        if not portfolio or hint not in portfolio[0]:
            portfolio = [hint, *portfolio]
    return portfolio, guarded


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
    total_amount: float,
    market_news: list[NewsItem] | None,
) -> dict[str, FundRecommendation]:
    mapping: dict[str, FundRecommendation] = {}
    for holding in request.holdings:
        weight = holding.holding_amount / total_amount * 100
        offline = build_offline_fund_recommendation(
            holding,
            weight,
            total_amount,
            request.profile,
            market_news=market_news,
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


def _max_allowed_bucket(risk: RiskAssessment, holding, request: AnalysisRequest) -> int:
    if risk.suggested_action == "risk_review":
        return 2
    if risk.level == "high":
        return 2
    if holding is not None and request.profile.avoid_chasing:
        sector = getattr(holding, "sector_return_percent", None)
        if sector is not None and sector > 5:
            return 2
    return 3


def _guard_portfolio_lines(lines: list[str], risk: RiskAssessment) -> list[str]:
    if risk.suggested_action != "risk_review":
        return lines

    mandatory = "组合已触发风险复核线，今日以控风险为先，不建议新增加仓。"
    if lines and mandatory in lines[0]:
        return lines
    return [mandatory, *lines]
