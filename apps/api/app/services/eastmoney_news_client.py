from __future__ import annotations

import json
import logging
import subprocess
import sys

from app.config import get_settings

logger = logging.getLogger(__name__)


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
    """在子进程中拉取基金公告。"""
    fund_code = fund_code.strip()
    if not fund_code:
        return []

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
        for _, row in frame.head(limit).iterrows():
            items.append({{str(k): (None if v != v else str(v)) for k, v in row.items()}})
        print(json.dumps({{"items": items}}))
except Exception as exc:
    print(json.dumps({{"error": str(exc), "items": []}}))
"""
    return _run_akshare_script(script, label=f"fund_announcement:{fund_code}")


def _run_akshare_script(script: str, *, label: str) -> list[dict]:
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
            return []
        payload = json.loads(result.stdout.strip())
        if payload.get("error"):
            logger.debug("eastmoney news subprocess error %s: %s", label, payload["error"])
        return payload.get("items") or []
    except subprocess.TimeoutExpired:
        logger.warning("eastmoney news subprocess timeout %s after %.0fs", label, timeout)
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("eastmoney news subprocess exception %s: %s", label, exc)
        return []
