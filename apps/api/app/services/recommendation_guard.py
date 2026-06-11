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
    tactical = request.profile.decision_style == "tactical"
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

        if offline is not None and not tactical and not reversal_note:
            normalized = conservative_action_text(normalized, offline.action)

        max_bucket = _max_allowed_bucket(risk, holding, request, tactical=tactical)
        if _action_bucket(normalized) > max_bucket:
            normalized = _BUCKET_TO_LABEL[_bucket_name(max_bucket)]

        if (
            not tactical
            and settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(normalized) >= 3
        ):
            normalized = "暂停追涨"
            max_bucket = min(max_bucket, 2)

        note = reversal_note
        if (
            not note
            and not tactical
            and settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(rec.action.strip()) >= 3
            and normalized != rec.action.strip()
        ):
            note = "无当日可引用要闻，已限制激进加仓类动作（更贴盘面、防幻觉）。"
        elif not note and offline is not None and not tactical and normalized != rec.action.strip():
            note = f"已按风控规则将「{rec.action.strip()}」调整为「{normalized}」（对照本地规则：{offline.action}）。"
        elif not note and tactical and normalized != rec.action.strip():
            note = f"战术模式下保留模型动作「{normalized}」（未与离线规则取更保守值）。"
        elif not note and normalized != rec.action.strip():
            note = f"已规范动作表述为「{normalized}」。"

        copy = rec.model_copy(update={"action": normalized})
        if note:
            copy.points = [note, *copy.points]
        guarded.append(copy)

    portfolio = _guard_portfolio_lines(portfolio_lines, risk)
    if not tactical and settings.news_require_today_for_add and not today_signal:
        hint = "当日无已引用要闻支撑，组合级建议以观察/控风险为主，不宜激进加仓。"
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
) -> int:
    if risk.suggested_action == "risk_review":
        return 2
    if risk.level == "high":
        return 2
    if (
        not tactical
        and holding is not None
        and request.profile.avoid_chasing
    ):
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
