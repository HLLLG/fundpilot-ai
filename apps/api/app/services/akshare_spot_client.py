from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

SpotBoard = dict[str, float]


def _clear_proxy_env() -> dict[str, str]:
    saved: dict[str, str] = {}
    for key in list(os.environ):
        if "proxy" in key.lower():
            saved[key] = os.environ.pop(key)
    os.environ["NO_PROXY"] = "*"
    return saved


def _restore_proxy_env(saved: dict[str, str]) -> None:
    os.environ.pop("NO_PROXY", None)
    os.environ.update(saved)


def _frame_to_board(frame: Any, name_col: str, change_col: str) -> SpotBoard:
    if frame is None or getattr(frame, "empty", True):
        return {}
    result: SpotBoard = {}
    for _, row in frame.iterrows():
        name = row.get(name_col)
        change = row.get(change_col)
        if name is None or change is None:
            continue
        cleaned = str(name).strip()
        if not cleaned:
            continue
        try:
            result[cleaned] = round(float(change), 4)
        except (TypeError, ValueError):
            continue
    return result


def _fetch_board_kind_subprocess(kind: str) -> SpotBoard:
    script = f"""
import json, os
for key in list(os.environ):
    if "proxy" in key.lower():
        os.environ.pop(key)
os.environ["NO_PROXY"] = "*"
import akshare as ak
kind = {kind!r}
result = {{}}
if kind == "concept":
    frame = ak.stock_board_concept_name_em()
    cols = ("板块名称", "涨跌幅")
elif kind == "industry":
    frame = ak.stock_board_industry_name_em()
    cols = ("板块名称", "涨跌幅")
else:
    raise SystemExit(1)
for _, row in frame.iterrows():
    name = str(row[cols[0]]).strip()
    if not name:
        continue
    try:
        result[name] = round(float(row[cols[1]]), 4)
    except (TypeError, ValueError):
        continue
print(json.dumps(result))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k): float(v) for k, v in payload.items()}


def fetch_boards_via_akshare() -> dict[str, SpotBoard]:
    """httpx 直连失败时，用 AkShare 列表接口兜底（概念/行业/指数）。"""
    boards: dict[str, SpotBoard] = {"concept": {}, "industry": {}, "index": {}}

    for kind in ("concept", "industry"):
        for attempt in range(3):
            fetched = _fetch_board_kind_subprocess(kind)
            if fetched:
                boards[kind] = fetched
                break
            if attempt + 1 < 3:
                time.sleep(0.8 * (attempt + 1))

    saved = _clear_proxy_env()
    try:
        import akshare as ak  # type: ignore[import-not-found]

        for symbol in ("沪深重要指数", "中证系列指数", "上证系列指数", "深证系列指数"):
            try:
                frame = ak.stock_zh_index_spot_em(symbol=symbol)
                boards["index"].update(_frame_to_board(frame, "名称", "涨跌幅"))
            except Exception as exc:
                logger.warning("akshare index board %s failed: %s", symbol, exc)
    except Exception as exc:
        logger.warning("akshare index fallback failed: %s", exc)
    finally:
        _restore_proxy_env(saved)

    return boards
