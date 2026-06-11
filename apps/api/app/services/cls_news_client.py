from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import date
from functools import lru_cache

from app.models import NewsItem

logger = logging.getLogger(__name__)

_CLS_TIMEOUT_SECONDS = 25


@lru_cache(maxsize=2)
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
        for _, row in frame.head({max(limit, 1)}).iterrows():
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
        return payload.get("items") or []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.debug("cls news fetch failed: %s", exc)
        return []


def search_cls_news(topic: str, limit: int = 5) -> list[NewsItem]:
    topic = topic.strip()
    if not topic:
        return []

    today = date.today().isoformat()
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
        published = _cell(row, "发布时间", "发布日期", "date", "time") or ""
        matched.append(
            NewsItem(
                topic=topic,
                title=title.strip(),
                published_at=published or None,
                source="cls",
                url=_cell(row, "链接", "url"),
                snippet=_truncate(body or title),
                is_today=_is_today(published, today),
            )
        )
        if len(matched) >= limit:
            break
    return matched


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


def _is_today(published: str | None, today: str) -> bool:
    if not published:
        return False
    return today in published[:10]


def _truncate(value: str, max_len: int = 200) -> str | None:
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"
