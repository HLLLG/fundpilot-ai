from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

_CACHE_FILENAME = "trade_dates.json"
_MAX_AGE_DAYS = 7
_SUBPROCESS_TIMEOUT = 45


def _cache_path() -> Path:
    return get_settings().db_path.parent / _CACHE_FILENAME


def _load_cached_dates() -> frozenset[str] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = date.fromisoformat(str(payload["fetched_at"])[:10])
        if date.today() - fetched > timedelta(days=_MAX_AGE_DAYS):
            return None
        return frozenset(str(value)[:10] for value in payload["dates"])
    except Exception:
        return None


def _save_cached_dates(dates: frozenset[str]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": date.today().isoformat(),
                "dates": sorted(dates),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _fetch_dates_subprocess() -> frozenset[str] | None:
    """在独立子进程拉取新浪交易日历，避免 py_mini_racer 拖垮 uvicorn 主进程。"""
    script = (
        "import akshare as ak, json; "
        "frame=ak.tool_trade_date_hist_sina(); "
        "column='trade_date' if 'trade_date' in frame.columns else frame.columns[0]; "
        "print(json.dumps([str(value)[:10] for value in frame[column].tolist()]))"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        dates = json.loads(completed.stdout.strip())
        if not dates:
            return None
        return frozenset(str(value)[:10] for value in dates)
    except Exception:
        return None


@lru_cache(maxsize=1)
def get_trade_date_set() -> frozenset[str] | None:
    cached = _load_cached_dates()
    if cached is not None:
        return cached

    fetched = _fetch_dates_subprocess()
    if fetched:
        _save_cached_dates(fetched)
        return fetched
    return None
