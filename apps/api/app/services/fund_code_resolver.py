from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache

from app.services.fund_name_utils import is_fund_name_match, normalize_fund_name

_SUBPROCESS_TIMEOUT = 120


def preload_fund_name_table() -> None:
    """启动时预热基金名称表，避免首次 OCR 查码卡顿。"""
    try:
        _fund_name_table()
    except Exception:
        pass


def _fetch_fund_name_table_subprocess() -> list[tuple[str, str]] | None:
    """在独立子进程拉取东财基金名称表，避免 py_mini_racer 与 PaddleOCR 同进程 crash。"""
    script = """
import akshare as ak
import json

try:
    frame = ak.fund_name_em()
    if frame is None or frame.empty:
        print(json.dumps([]))
    else:
        code_col = "基金代码" if "基金代码" in frame.columns else frame.columns[0]
        name_col = "基金简称" if "基金简称" in frame.columns else frame.columns[1]
        rows = []
        for _, row in frame.iterrows():
            code = str(row[code_col]).strip().zfill(6)
            name = str(row[name_col]).strip()
            if code and name:
                rows.append([code, name])
        print(json.dumps(rows, ensure_ascii=False))
except Exception:
    print(json.dumps([]))
"""
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
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, list):
            return None
        return [(str(code), str(name)) for code, name in payload if code and name]
    except Exception:
        return None


@lru_cache(maxsize=1)
def _fund_name_table() -> list[tuple[str, str]]:
    fetched = _fetch_fund_name_table_subprocess()
    return fetched or []


def resolve_holding_fund_code(
    fund_name: str,
    *,
    existing_code: str | None = None,
) -> tuple[str | None, str | None]:
    """按名称查码。返回 (code, source)；source 为 akshare | None。"""
    if existing_code and existing_code != "000000":
        return existing_code, None
    code = lookup_fund_code_by_name(fund_name)
    if code:
        return code, "akshare"
    return None, None


def lookup_fund_code_by_name(fund_name: str) -> str | None:
    target = normalize_fund_name(fund_name)
    if not target:
        return None

    best_code: str | None = None
    best_score = 0
    for code, name in _fund_name_table():
        normalized = normalize_fund_name(name)
        if not normalized:
            continue
        if target == normalized:
            return code
        if is_fund_name_match(target, normalized):
            score = min(len(target), len(normalized))
            if score > best_score:
                best_score = score
                best_code = code
    return best_code
