from __future__ import annotations

from app.models import DiscoveryRecommendation, InvestorProfile, NewsItem, TopicBrief
from app.services.news_citation import _collect_citable_titles, _matches_known_title


def apply_discovery_guards(
    recommendations: list[DiscoveryRecommendation],
    *,
    candidate_pool: list[dict],
    held_codes: set[str],
    profile: InvestorProfile,
    budget_yuan: float,
    sector_heat: list[dict],
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
) -> tuple[list[DiscoveryRecommendation], list[str]]:
    allowed_codes = {str(item.get("fund_code", "")).zfill(6) for item in candidate_pool}
    pool_by_code = {
        str(item.get("fund_code", "")).zfill(6): item for item in candidate_pool
    }
    heat_by_sector = {
        str(row.get("sector_label", "")): row.get("change_1d_percent")
        for row in sector_heat
    }
    titles = _collect_citable_titles(market_news or [], topic_briefs or [])
    caveats: list[str] = []
    guarded: list[DiscoveryRecommendation] = []

    for rec in recommendations:
        code = rec.fund_code.strip().zfill(6)
        if code not in allowed_codes:
            caveats.append(f"已剔除池外基金 {code}（{rec.fund_name}）。")
            continue
        if code in held_codes:
            caveats.append(f"已持有 {code}，不作为新买入推荐。")
            continue

        copy = rec.model_copy(deep=True)
        sector_move = heat_by_sector.get(copy.sector_name)
        if profile.avoid_chasing and sector_move is not None and sector_move >= 4.0:
            if copy.action == "分批买入":
                copy.action = "等待回调"
                copy.points = list(copy.points) + [
                    f"板块当日 {sector_move:+.2f}% 偏热，拒绝追高模式下建议等待回调。"
                ]

        if profile.avoid_chasing and copy.action == "分批买入":
            pool_item = pool_by_code.get(code, {})
            r1y = pool_item.get("return_1y_percent")
            nav_trend = pool_item.get("nav_trend") or {}
            dist_high = nav_trend.get("distance_from_high_percent")
            if r1y is not None and float(r1y) >= 100.0:
                copy.action = "等待回调"
                copy.points = list(copy.points) + [
                    f"近1年涨幅 {float(r1y):+.1f}% 偏高，拒绝追高模式下建议等待回调。"
                ]
            elif dist_high is not None and float(dist_high) > -5.0:
                copy.action = "等待回调"
                copy.points = list(copy.points) + [
                    f"净值距区间高点仅 {float(dist_high):+.1f}%，短线追高风险偏高。"
                ]

        max_single = budget_yuan * profile.concentration_limit_percent / 100
        if copy.suggested_amount_yuan is not None and max_single > 0:
            if copy.suggested_amount_yuan > max_single:
                copy.suggested_amount_yuan = round(max_single, 0)
                copy.amount_note = (
                    f"示意金额已压至单只集中度上限约 {profile.concentration_limit_percent:.0f}%"
                )

        copy.news_bullish = _filter_news_titles(copy.news_bullish, titles)
        guarded.append(copy)

    return guarded[:5], caveats


def _filter_news_titles(headlines: list[str], known_titles: list[str]) -> list[str]:
    cleaned: list[str] = []
    for headline in headlines:
        text = headline.strip()
        if not text:
            continue
        if known_titles and not _matches_known_title(text, known_titles):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:3]
