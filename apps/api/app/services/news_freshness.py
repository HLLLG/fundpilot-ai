from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models import NewsItem, TopicBrief

CN_TZ = ZoneInfo("Asia/Shanghai")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class NewsPublishedTime:
    """A publication time normalized to the decision timezone.

    Date-only values deliberately keep ``moment`` unset so callers can rank
    them behind same-day articles whose publication time is known.
    """

    moment: datetime | None
    calendar_date: date | None
    has_time: bool


def normalize_news_now(now: datetime | None = None) -> datetime:
    resolved = now or datetime.now(CN_TZ)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=CN_TZ)
    return resolved.astimezone(CN_TZ)


def resolve_decision_local_datetime(session: dict | None = None) -> str:
    """Use one session clock across system prompts, user payloads and guards."""
    resolved_session = session or {}
    local_datetime = str(resolved_session.get("local_datetime") or "").strip()
    if local_datetime:
        return local_datetime
    calendar_date = str(resolved_session.get("calendar_date") or "").strip()
    if calendar_date:
        return calendar_date
    return normalize_news_now().strftime("%Y-%m-%d %H:%M")


def parse_news_published_at(published_at: str | None) -> NewsPublishedTime:
    """Parse provider timestamps without treating UTC dates as China dates."""
    text = str(published_at or "").strip()
    if not text:
        return NewsPublishedTime(moment=None, calendar_date=None, has_time=False)

    if _DATE_ONLY_RE.fullmatch(text):
        try:
            return NewsPublishedTime(
                moment=None,
                calendar_date=date.fromisoformat(text),
                has_time=False,
            )
        except ValueError:
            return NewsPublishedTime(moment=None, calendar_date=None, has_time=False)

    candidate = text[:-1] + "+00:00" if text.upper().endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return NewsPublishedTime(moment=None, calendar_date=None, has_time=False)

    if parsed.tzinfo is None:
        normalized = parsed.replace(tzinfo=CN_TZ)
    else:
        normalized = parsed.astimezone(CN_TZ)
    return NewsPublishedTime(
        moment=normalized,
        calendar_date=normalized.date(),
        has_time=True,
    )


def is_news_published_today(
    published_at: str | None,
    now: datetime | None = None,
) -> bool:
    parsed = parse_news_published_at(published_at)
    if parsed.calendar_date is None:
        return False
    return parsed.calendar_date == normalize_news_now(now).date()


def latest_news_published_at(items: list[NewsItem] | None) -> str | None:
    """Return the newest parseable publication value using calendar-first order."""
    latest_value: str | None = None
    latest_sort: tuple[int, int, float] | None = None
    for item in items or []:
        published = str(item.published_at or "").strip()
        if not published:
            continue
        sort_value = _published_sort_value(parse_news_published_at(published))
        if sort_value is None:
            continue
        if latest_sort is None or sort_value > latest_sort:
            latest_sort = sort_value
            latest_value = published
    return latest_value


def build_news_pipeline_context(
    market_news: list[NewsItem] | None,
    topic_briefs: list[TopicBrief] | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """Summarize news timeliness for DeepSeek and diagnostics."""
    items = market_news or []
    resolved_now = normalize_news_now(now)
    today_iso = resolved_now.date().isoformat()

    ages_minutes: list[int] = []
    today_items: list[NewsItem] = []
    for item in items:
        parsed = parse_news_published_at(item.published_at)
        is_today = (
            parsed.calendar_date == resolved_now.date()
            if parsed.calendar_date is not None
            else item.is_today
        )
        if not is_today:
            continue
        today_items.append(item)
        age = _age_minutes(item.published_at, resolved_now)
        if age is not None:
            ages_minutes.append(age)

    freshness = _freshness_label(ages_minutes, len(today_items), len(items))

    topics: dict[str, dict] = {}
    for item in items:
        parsed = parse_news_published_at(item.published_at)
        is_today = (
            parsed.calendar_date == resolved_now.date()
            if parsed.calendar_date is not None
            else item.is_today
        )
        topic_names = dict.fromkeys([item.topic, *item.related_topics])
        for topic in topic_names:
            if not topic:
                continue
            bucket = topics.setdefault(
                topic,
                {
                    "topic": topic,
                    "total": 0,
                    "today_count": 0,
                    "latest_published_at": None,
                    "_latest_sort": None,
                },
            )
            bucket["total"] += 1
            if is_today:
                bucket["today_count"] += 1
            latest_sort = _published_sort_value(parsed)
            if latest_sort is not None and (
                bucket["_latest_sort"] is None or latest_sort > bucket["_latest_sort"]
            ):
                bucket["_latest_sort"] = latest_sort
                bucket["latest_published_at"] = item.published_at

    for bucket in topics.values():
        bucket.pop("_latest_sort", None)

    brief_today_points = sum(
        1 for brief in topic_briefs or [] for point in brief.points if point.is_today
    )

    return {
        "as_of": resolved_now.strftime("%Y-%m-%d %H:%M"),
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
    parsed = parse_news_published_at(published_at)
    if parsed.moment is None:
        return None
    return max(0, int((normalize_news_now(now) - parsed.moment).total_seconds() // 60))


def _published_sort_value(parsed: NewsPublishedTime) -> tuple[int, int, float] | None:
    if parsed.calendar_date is None:
        return None
    # Date is the primary freshness dimension. A timestamp is only more precise
    # than a date-only value from the *same* day; it must not make an older article
    # appear newer than a later date-only article.
    if parsed.moment is not None:
        return (parsed.calendar_date.toordinal(), 1, parsed.moment.timestamp())
    if parsed.calendar_date is not None:
        return (parsed.calendar_date.toordinal(), 0, 0.0)
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
