from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import NewsItem, TopicBrief

CN_TZ = ZoneInfo("Asia/Shanghai")


def build_news_pipeline_context(
    market_news: list[NewsItem] | None,
    topic_briefs: list[TopicBrief] | None = None,
) -> dict:
    """Summarize news timeliness for DeepSeek and diagnostics."""
    items = market_news or []
    now = datetime.now(CN_TZ)
    today_iso = now.date().isoformat()

    ages_minutes: list[int] = []
    today_items: list[NewsItem] = []
    for item in items:
        if not item.is_today:
            continue
        today_items.append(item)
        age = _age_minutes(item.published_at, now)
        if age is not None:
            ages_minutes.append(age)

    freshness = _freshness_label(ages_minutes, len(today_items), len(items))

    topics: dict[str, dict] = {}
    for item in items:
        bucket = topics.setdefault(
            item.topic,
            {"topic": item.topic, "total": 0, "today_count": 0, "latest_published_at": None},
        )
        bucket["total"] += 1
        if item.is_today:
            bucket["today_count"] += 1
        published = item.published_at
        if published and (
            bucket["latest_published_at"] is None
            or str(published) > str(bucket["latest_published_at"])
        ):
            bucket["latest_published_at"] = published

    brief_today_points = sum(
        1 for brief in topic_briefs or [] for point in brief.points if point.is_today
    )

    return {
        "as_of": now.strftime("%Y-%m-%d %H:%M"),
        "calendar_date": today_iso,
        "total_items": len(items),
        "today_items": len(today_items),
        "today_ratio": round(len(today_items) / len(items), 2) if items else 0.0,
        "brief_today_point_count": brief_today_points,
        "has_today_signal": len(today_items) > 0 or brief_today_points > 0,
        "freshness_label": freshness,
        "median_age_minutes": _median(ages_minutes),
        "max_age_minutes": max(ages_minutes) if ages_minutes else None,
        "min_age_minutes": min(ages_minutes) if ages_minutes else None,
        "topics": list(topics.values()),
        "interpretation": _interpretation(freshness, len(today_items), len(items)),
    }


def _age_minutes(published_at: str | None, now: datetime) -> int | None:
    if not published_at:
        return None
    text = published_at.strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            moment = datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
            return max(0, int((now - moment).total_seconds() // 60))
        except ValueError:
            continue
    return None


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def _freshness_label(
    ages_minutes: list[int],
    today_count: int,
    total_count: int,
) -> str:
    if total_count == 0:
        return "empty"
    if today_count == 0:
        return "stale"
    if not ages_minutes:
        return "today_unknown_time"
    median = _median(ages_minutes)
    if median is None:
        return "today_unknown_time"
    if median <= 120:
        return "fresh"
    if median <= 360:
        return "moderate"
    return "aging"


def _interpretation(freshness: str, today_count: int, total_count: int) -> str:
    if total_count == 0:
        return "未拉取到任何新闻，模型只能依赖板块涨跌与净值摘要。"
    if today_count == 0:
        return "预取新闻均为非当日，加仓类建议会被守卫压为保守动作。"
    mapping = {
        "fresh": "当日新闻较新（中位龄≤2小时），适合支撑收盘前战术判断。",
        "moderate": "有当日新闻但部分偏旧（中位龄2–6小时），须结合实时板块涨跌交叉验证。",
        "aging": "当日新闻整体偏旧（中位龄>6小时），不宜单独作为追涨依据。",
        "today_unknown_time": "有当日新闻但发布时间解析不全，请人工核对 prefetched_news。",
        "stale": "无当日新闻。",
        "empty": "无新闻。",
    }
    return mapping.get(freshness, "新闻时效一般，请结合盘面。")
