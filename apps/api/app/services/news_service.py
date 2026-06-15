from __future__ import annotations

import re
from datetime import date

from app.config import get_settings
from app.models import Holding, NewsItem
from app.services.news_cache import NEWS_CACHE_STALE_SECONDS, get_cached_news, save_cached_news
from app.services.trading_session import build_trading_session

_SNIPPET_MAX_LEN = 200
_TOPIC_ALIASES = ("人工智能", "电网设备", "半导体", "国防军工", "商业航天")


class NewsService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def topics_from_holdings(
        self,
        holdings: list[Holding],
        max_topics: int | None = None,
    ) -> list[str]:
        seen: set[str] = set()
        topics: list[str] = []
        limit = max_topics if max_topics is not None else self.settings.news_max_topics

        for holding in holdings:
            candidates = [
                _normalize_topic(holding.sector_name),
                _keyword_from_name(holding.fund_name),
            ]
            for topic in candidates:
                if not topic or topic in seen:
                    continue
                seen.add(topic)
                topics.append(topic)

        sources = self.settings.news_source_set
        if holdings and "macro" in sources:
            macro = self.settings.news_macro_topic.strip()
            if macro and macro not in seen:
                topics.insert(0, macro)

        return topics[:limit]

    def search(self, topic: str, limit: int | None = None) -> list[NewsItem]:
        topic = topic.strip()
        if not topic or not self.settings.news_enabled:
            return []

        per_topic = limit if limit is not None else self.settings.news_per_topic
        per_topic = max(1, min(per_topic, 10))

        cached = get_cached_news(topic, max_age_seconds=_news_cache_max_age_seconds())
        if cached is not None:
            return cached[:per_topic]

        sources = self.settings.news_source_set
        items: list[NewsItem] = []

        if re.fullmatch(r"\d{6}", topic) and "announcement" in sources:
            items.extend(self._from_fund_announcements(topic, per_topic))

        if "eastmoney" in sources or "macro" in sources:
            items.extend(self._from_eastmoney(topic, per_topic * 2))

        if "cls" in sources:
            items.extend(self._from_cls(topic, per_topic))

        ranked = _rank_news_by_recency(_dedupe_news(items))[:per_topic]
        if ranked:
            save_cached_news(topic, ranked)
        return ranked

    def prefetch_topics(self, topics: list[str]) -> list[NewsItem]:
        if not self.settings.news_enabled:
            return []

        collected: list[NewsItem] = []
        for topic in topics[: self.settings.news_max_topics]:
            collected.extend(self.search(topic))
        return _rank_news_by_recency(_dedupe_news(collected))

    def prefetch_for_holdings(
        self,
        holdings: list[Holding],
        max_topics: int | None = None,
    ) -> list[NewsItem]:
        topics = self.topics_from_holdings(holdings, max_topics=max_topics)
        return self.prefetch_topics(topics)

    def _from_eastmoney(self, topic: str, limit: int) -> list[NewsItem]:
        try:
            import akshare as ak  # type: ignore[import-not-found]

            frame = ak.stock_news_em(symbol=topic)
        except Exception:
            return []

        if frame is None or frame.empty:
            return []

        today = date.today().isoformat()
        items: list[NewsItem] = []
        for _, row in frame.head(limit).iterrows():
            title = _cell(row, "新闻标题", "title")
            if not title:
                continue
            published = _optional_str(_cell(row, "发布时间", "date"))
            snippet = _cell(row, "新闻内容", "content")
            items.append(
                NewsItem(
                    topic=topic,
                    title=str(title).strip(),
                    published_at=published,
                    source=_optional_str(_cell(row, "文章来源", "mediaName")) or "eastmoney",
                    url=_optional_str(_cell(row, "新闻链接", "url")),
                    snippet=_truncate(snippet),
                    is_today=_is_today(published, today),
                )
            )
        return items

    def _from_cls(self, topic: str, limit: int) -> list[NewsItem]:
        try:
            from app.services.cls_news_client import search_cls_news

            return search_cls_news(topic, limit=limit)
        except Exception:
            return []

    def _from_fund_announcements(self, fund_code: str, limit: int) -> list[NewsItem]:
        try:
            import akshare as ak  # type: ignore[import-not-found]

            frame = ak.fund_announcement_report_em(symbol=fund_code)
        except Exception:
            return []

        if frame is None or frame.empty:
            return []

        today = date.today().isoformat()
        items: list[NewsItem] = []
        for _, row in frame.head(limit).iterrows():
            title = _cell(row, "公告标题", "title")
            if not title:
                continue
            published = _cell(row, "公告日期", "date")
            if published and hasattr(published, "isoformat"):
                published = published.isoformat()
            published_str = _optional_str(published)
            items.append(
                NewsItem(
                    topic=fund_code,
                    title=str(title).strip(),
                    published_at=published_str,
                    source="fund-announcement",
                    url=None,
                    snippet=_truncate(_cell(row, "基金名称", "fund_name")),
                    is_today=_is_today(published_str, today),
                )
            )
        return items


def _normalize_topic(topic: str | None) -> str | None:
    if not topic:
        return None
    cleaned = topic.strip()
    for prefix in ("中证", "国证", "上证"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned or None


def _keyword_from_name(name: str) -> str | None:
    cleaned = name.replace("...", "").replace(".", "").strip()
    for token in _TOPIC_ALIASES:
        if token in cleaned:
            return token
    return None


def _is_today(published: str | None, today: str) -> bool:
    if not published:
        return False
    return today in published[:10]


def _rank_news_by_recency(items: list[NewsItem]) -> list[NewsItem]:
    def sort_key(item: NewsItem) -> tuple[int, str]:
        today_rank = 0 if item.is_today else 1
        return (today_rank, item.published_at or "")

    return sorted(items, key=sort_key)


def _cell(row: object, *names: str) -> str | None:
    for name in names:
        if hasattr(row, "index") and name in row.index:  # type: ignore[attr-defined]
            value = row[name]  # type: ignore[index]
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _truncate(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    if len(cleaned) <= _SNIPPET_MAX_LEN:
        return cleaned
    return cleaned[: _SNIPPET_MAX_LEN - 1] + "…"


def _dedupe_news(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        key = item.url or f"{item.topic}:{item.title}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _news_cache_max_age_seconds() -> int | None:
    session = build_trading_session()
    session_kind = str(session.get("session_kind") or "")
    if session_kind in {"trading_day_intraday", "trading_day_pre_open"}:
        return NEWS_CACHE_STALE_SECONDS
    return None
