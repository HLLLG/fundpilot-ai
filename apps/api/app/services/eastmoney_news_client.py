from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.config import get_settings

logger = logging.getLogger(__name__)

NewsFetchStatus = Literal["ok", "empty", "error", "timeout"]


@dataclass(frozen=True)
class EastmoneyNewsFetchResult:
    """Provider outcome that keeps an empty response distinct from failure."""

    status: NewsFetchStatus
    items: list[dict]


def _timeout_seconds() -> float:
    return float(max(5.0, get_settings().news_fetch_timeout_seconds))


def fetch_stock_news_em(topic: str, *, limit: int) -> list[dict]:
    """在子进程中拉取东方财富个股/主题新闻，避免 AkShare 阻塞主进程。"""
    topic = topic.strip()
    if not topic:
        return []

    topic_literal = json.dumps(topic, ensure_ascii=False)
    row_limit = max(1, min(int(limit), 30))
    script = f"""
import akshare as ak
import json

topic = {topic_literal}
limit = {row_limit}
try:
    frame = ak.stock_news_em(symbol=topic)
    if frame is None or frame.empty:
        print(json.dumps({{"items": []}}))
    else:
        items = []
        for _, row in frame.head(limit).iterrows():
            items.append({{str(k): (None if v != v else str(v)) for k, v in row.items()}})
        print(json.dumps({{"items": items}}))
except Exception as exc:
    print(json.dumps({{"error": str(exc), "items": []}}))
"""
    return _run_akshare_script(script, label=f"stock_news:{topic}")


def fetch_fund_announcement_report_em(fund_code: str, *, limit: int) -> list[dict]:
    """兼容旧调用方的 list 接口；结构化状态使用 result 版本。"""
    return fetch_fund_announcement_report_result_em(fund_code, limit=limit).items


def fetch_fund_announcement_report_result_em(
    fund_code: str,
    *,
    limit: int,
) -> EastmoneyNewsFetchResult:
    """在子进程中拉取基金公告，并保留 empty/error/timeout 语义。"""
    fund_code = fund_code.strip()
    if not fund_code:
        return EastmoneyNewsFetchResult(status="empty", items=[])

    code_literal = json.dumps(fund_code, ensure_ascii=False)
    row_limit = max(1, min(int(limit), 20))
    script = f"""
import akshare as ak
import json

fund_code = {code_literal}
limit = {row_limit}
try:
    frame = ak.fund_announcement_report_em(symbol=fund_code)
    if frame is None or frame.empty:
        print(json.dumps({{"items": []}}))
    else:
        items = []
        for _, row in frame.iterrows():
            items.append({{str(k): (None if v != v else str(v)) for k, v in row.items()}})
        print(json.dumps({{"items": items}}))
except Exception as exc:
    print(json.dumps({{"error": str(exc), "items": []}}))
"""
    result = _run_akshare_script_result(script, label=f"fund_announcement:{fund_code}")
    if result.status != "ok":
        return result
    return EastmoneyNewsFetchResult(
        status="ok",
        items=_latest_announcement_rows(result.items, limit=row_limit),
    )


def _latest_announcement_rows(rows: list[dict], *, limit: int) -> list[dict]:
    """Return newest announcements first while preserving invalid-date row order."""

    indexed = list(enumerate(rows))

    def sort_key(entry: tuple[int, dict]) -> tuple[int, int, int]:
        index, row = entry
        published = _announcement_date(row)
        if published is None:
            return (1, 0, index)
        return (0, -published.toordinal(), index)

    return [row for _, row in sorted(indexed, key=sort_key)[: max(0, limit)]]


def _announcement_date(row: dict) -> date | None:
    for key in ("公告日期", "公告时间", "发布时间", "日期", "date"):
        raw = row.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError:
                continue
    return None


def _run_akshare_script(script: str, *, label: str) -> list[dict]:
    return _run_akshare_script_result(script, label=label).items


def _run_akshare_script_result(
    script: str,
    *,
    label: str,
) -> EastmoneyNewsFetchResult:
    timeout = _timeout_seconds()
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.debug("eastmoney news subprocess failed %s stderr=%s", label, result.stderr[:200])
            return EastmoneyNewsFetchResult(status="error", items=[])
        payload = json.loads(result.stdout.strip())
        if not isinstance(payload, dict):
            return EastmoneyNewsFetchResult(status="error", items=[])
        if payload.get("error"):
            logger.debug("eastmoney news subprocess error %s: %s", label, payload["error"])
            return EastmoneyNewsFetchResult(status="error", items=[])
        items = payload.get("items") or []
        if not isinstance(items, list):
            return EastmoneyNewsFetchResult(status="error", items=[])
        return EastmoneyNewsFetchResult(
            status="ok" if items else "empty",
            items=items,
        )
    except subprocess.TimeoutExpired:
        logger.warning("eastmoney news subprocess timeout %s after %.0fs", label, timeout)
        return EastmoneyNewsFetchResult(status="timeout", items=[])
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        logger.debug("eastmoney news subprocess exception %s: %s", label, exc)
        return EastmoneyNewsFetchResult(status="error", items=[])
