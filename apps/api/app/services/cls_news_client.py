from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime

from app.models import NewsItem
from app.services.news_freshness import (
    is_news_published_today,
    normalize_news_now,
    parse_news_published_at,
)

logger = logging.getLogger(__name__)

_CLS_TIMEOUT_SECONDS = 25


def fetch_cls_headlines(limit: int = 40) -> list[dict]:
    script = f"""
import akshare as ak
import json
try:
    frame = ak.stock_info_global_cls()
    if frame is None or frame.empty:
        print(json.dumps({{"items": []}}))
    else:
        items = []
        for _, row in frame.iterrows():
            items.append({{str(k): (None if v != v else str(v)) for k, v in row.items()}})
        print(json.dumps({{"items": items}}))
except Exception as e:
    print(json.dumps({{"error": str(e), "items": []}}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_CLS_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        payload = json.loads(result.stdout.strip())
        items = payload.get("items") or []
        return _rank_cls_rows(items)[: max(limit, 1)]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.debug("cls news fetch failed: %s", exc)
        return []


def search_cls_news(
    topic: str,
    limit: int = 5,
    *,
    now: datetime | None = None,
) -> list[NewsItem]:
    topic = topic.strip()
    if not topic:
        return []

    resolved_now = normalize_news_now(now)
    keywords = _topic_keywords(topic)
    matched: list[NewsItem] = []
    for row in fetch_cls_headlines(limit=60):
        title = _cell(row, "标题", "title", "内容")
        if not title:
            continue
        body = _cell(row, "内容", "content", "摘要") or ""
        text = f"{title} {body}"
        if not any(keyword in text for keyword in keywords):
            continue
        published = _published_at(row)
        matched.append(
            NewsItem(
                topic=topic,
                title=title.strip(),
                published_at=published or None,
                source="cls",
                url=_cell(row, "链接", "url"),
                snippet=_truncate(body or title),
                is_today=is_news_published_today(published, resolved_now),
            )
        )
    return _rank_cls_items(matched)[: max(limit, 1)]


def _topic_keywords(topic: str) -> list[str]:
    base = [topic]
    for token in ("人工智能", "电网设备", "半导体", "国防军工", "商业航天", "白酒", "新能源"):
        if token in topic and token not in base:
            base.append(token)
    if topic in {"上证指数", "宏观"}:
        base.extend(["A股", "沪指", "大盘", "指数"])
    return base


def _cell(row: dict, *names: str) -> str | None:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
    return None


def _published_at(row: dict) -> str:
    """Merge the split date/time shape returned by ``stock_info_global_cls``."""
    date_part = _cell(row, "发布日期", "date")
    time_part = _cell(row, "发布时间", "time")
    if date_part and time_part:
        parsed_date = parse_news_published_at(date_part)
        if parsed_date.calendar_date is not None and not parsed_date.has_time:
            combined = f"{date_part} {time_part}"
            if parse_news_published_at(combined).calendar_date is not None:
                return combined
        if parse_news_published_at(time_part).calendar_date is not None:
            return time_part
    return date_part or time_part or ""


def _published_sort_key(value: str | None) -> tuple[int, int, float]:
    parsed = parse_news_published_at(value)
    if parsed.calendar_date is None:
        return (-1, 0, float("-inf"))
    return (
        parsed.calendar_date.toordinal(),
        1 if parsed.moment is not None else 0,
        parsed.moment.timestamp() if parsed.moment is not None else 0.0,
    )


def _rank_cls_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            _published_sort_key(_published_at(row)),
            str(_cell(row, "标题", "title") or ""),
        ),
        reverse=True,
    )


def _rank_cls_items(items: list[NewsItem]) -> list[NewsItem]:
    return sorted(
        items,
        key=lambda item: (
            _published_sort_key(item.published_at),
            item.title,
        ),
        reverse=True,
    )


def _truncate(value: str, max_len: int = 200) -> str | None:
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"
