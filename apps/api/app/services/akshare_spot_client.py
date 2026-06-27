from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any

from app.services.akshare_subprocess import run_akshare_json_script

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
import json, os, sys
# 清除所有代理环境变量，确保子进程直连
for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
os.environ.pop("REQUESTS_CA_BUNDLE", None)
os.environ.pop("CURL_CA_BUNDLE", None)

try:
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
    if frame is None or frame.empty:
        print(json.dumps(result))
        sys.exit(0)
    for _, row in frame.iterrows():
        name = str(row[cols[0]]).strip()
        if not name:
            continue
        try:
            result[name] = round(float(row[cols[1]]), 4)
        except (TypeError, ValueError):
            continue
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{}}))
    sys.exit(1)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        logger.debug("akshare subprocess failed: %s", completed.stderr[:200] if completed.stderr else "no output")
        return {}
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as e:
        logger.debug("akshare subprocess JSON parse failed: %s", e)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k): float(v) for k, v in payload.items()}


def fetch_akshare_board_records(board_type: str) -> list[dict[str, Any]]:
    """AkShare 子进程拉经典行业/概念（仅涨跌幅；主力净流入可能为空）。"""
    if board_type not in {"industry", "concept"}:
        return []
    spot = _fetch_board_kind_subprocess(board_type)
    if not spot:
        return []
    return [
        {
            "name": name,
            "code": None,
            "change_percent": change,
            "main_force_net_yi": None,
        }
        for name, change in spot.items()
    ]


def _fetch_index_boards_subprocess() -> SpotBoard:
    script = """
import json
import os
import sys

for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
os.environ.pop("REQUESTS_CA_BUNDLE", None)
os.environ.pop("CURL_CA_BUNDLE", None)

try:
    import akshare as ak

    result = {}
    for symbol in ("沪深重要指数", "中证系列指数", "上证系列指数", "深证系列指数"):
        frame = ak.stock_zh_index_spot_em(symbol=symbol)
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            name = str(row.get("名称", "")).strip()
            if not name:
                continue
            try:
                result[name] = round(float(row.get("涨跌幅")), 4)
            except (TypeError, ValueError):
                continue
    print(json.dumps({"data": result}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e)}, ensure_ascii=False))
    sys.exit(1)
"""
    payload = run_akshare_json_script(
        script,
        label="index boards",
        timeout=120,
    )
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    result: SpotBoard = {}
    for name, change in data.items():
        try:
            result[str(name)] = round(float(change), 4)
        except (TypeError, ValueError):
            continue
    return result


def fetch_boards_via_akshare(*, include_index: bool = True) -> dict[str, SpotBoard]:
    """httpx 直连失败时，用 AkShare 列表接口兜底（概念/行业；指数可选且较慢）。"""
    boards: dict[str, SpotBoard] = {"concept": {}, "industry": {}, "index": {}}

    for kind in ("concept", "industry"):
        for attempt in range(3):
            fetched = _fetch_board_kind_subprocess(kind)
            if fetched:
                boards[kind] = fetched
                break
            if attempt + 1 < 3:
                time.sleep(0.8 * (attempt + 1))

    if not include_index:
        return boards

    boards["index"].update(_fetch_index_boards_subprocess())

    return boards
