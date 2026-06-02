from __future__ import annotations

from app.models import NewsItem, TopicBrief


def count_today_news(market_news: list[NewsItem] | None) -> int:
    if not market_news:
        return 0
    return sum(1 for item in market_news if item.is_today)


def has_today_market_signal(
    market_news: list[NewsItem] | None,
    topic_briefs: list[TopicBrief] | None = None,
) -> bool:
    if count_today_news(market_news) > 0:
        return True
    for brief in topic_briefs or []:
        if any(point.is_today for point in brief.points):
            return True
    return False
