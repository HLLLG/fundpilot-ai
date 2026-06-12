from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache

from app.services.fund_name_utils import (
    extract_share_class_letter,
    is_fund_name_match,
    lookup_match_score,
    normalize_fund_name_for_lookup,
)

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
    """按名称查码。已有非临时档案码且 OCR 未指明不同份额时，优先沿用档案。"""
    target = normalize_fund_name_for_lookup(fund_name)
    ocr_share_class = extract_share_class_letter(fund_name)

    if (
        existing_code
        and existing_code != "000000"
        and not is_provisional_fund_code(existing_code)
        and target
    ):
        profile_name = lookup_fund_name_by_code(existing_code)
        if profile_name and is_fund_name_match(
            target, normalize_fund_name_for_lookup(profile_name)
        ):
            profile_share_class = extract_share_class_letter(profile_name)
            if ocr_share_class is None or ocr_share_class == profile_share_class:
                return existing_code, "profile"

    looked_up = lookup_fund_code_by_name(fund_name)
    if looked_up:
        return looked_up, "akshare"
    if existing_code and existing_code != "000000" and not is_provisional_fund_code(existing_code):
        return existing_code, "profile"
    return None, None


def lookup_fund_code_by_name(fund_name: str) -> str | None:
    target = normalize_fund_name_for_lookup(fund_name)
    if not target:
        return None

    target_class = extract_share_class_letter(fund_name)

    for code, name in _fund_name_table():
        normalized = normalize_fund_name_for_lookup(name)
        if normalized and target == normalized:
            return code

    candidates: list[tuple[int, str]] = []
    for code, name in _fund_name_table():
        normalized = normalize_fund_name_for_lookup(name)
        if not normalized or not is_fund_name_match(target, normalized):
            continue
        table_class = extract_share_class_letter(name)
        if target_class and table_class and target_class != table_class:
            continue
        score = lookup_match_score(target, normalized)
        if score > 0:
            candidates.append((score, code))

    if not candidates:
        return None

    if target_class is None:
        class_by_code = {
            code: extract_share_class_letter(name)
            for code, name in _fund_name_table()
        }
        c_only = [item for item in candidates if class_by_code.get(item[1]) == "C"]
        # 支付宝总览常见 C 类份额；仅在 A/C 并存且 OCR 未识别份额时启用
        if len(c_only) == 1 and len(candidates) >= 2:
            return c_only[0][1]

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score = candidates[0][0]
    top = [code for score, code in candidates if score == best_score]
    if len(top) == 1:
        return top[0]
    return None


def lookup_fund_name_by_code(fund_code: str) -> str | None:
    code = fund_code.strip().zfill(6)
    if len(code) != 6 or not code.isdigit():
        return None
    for table_code, name in _fund_name_table():
        if table_code == code:
            return name
    return None


def search_funds_by_keyword(keyword: str, *, limit: int = 12) -> list[dict[str, str]]:
    """东财基金表模糊搜索，供确认页手动选码（养基宝式核对）。"""
    query = keyword.strip()
    if not query:
        return []

    table = _fund_name_table()
    results: list[tuple[int, str, str]] = []

    if query.isdigit() and len(query) <= 6:
        code_query = query.zfill(6)
        for code, name in table:
            if code == code_query:
                return [{"fund_code": code, "fund_name": name}]
            if code.startswith(query):
                results.append((900_000 + len(query), code, name))

    query_norm = normalize_fund_name_for_lookup(query)
    for code, name in table:
        if query in name:
            score = 500_000 + len(query)
        elif query_norm:
            score = lookup_match_score(query_norm, normalize_fund_name_for_lookup(name))
        else:
            score = 0
        if score > 0:
            results.append((score, code, name))

    results.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    payload: list[dict[str, str]] = []
    for _, code, name in results:
        if code in seen:
            continue
        seen.add(code)
        payload.append({"fund_code": code, "fund_name": name})
        if len(payload) >= limit:
            break
    return payload


def is_provisional_fund_code(fund_code: str | None) -> bool:
    """9xxxxx 为名称查码失败时的临时占位，不是真实基金代码。"""
    if not fund_code or len(fund_code) != 6:
        return False
    return fund_code.startswith("9") and fund_code != "000000"


def reconcile_holding_fund_codes(holdings: list) -> list:
    """页面加载/OCR 后：用东财名称表纠正临时码或旧 profile 误码。"""
    from app.models import Holding
    from app.services.fund_name_utils import sanitize_fund_name

    reconciled: list = []
    for holding in holdings:
        item = holding if isinstance(holding, Holding) else Holding.model_validate(holding)
        clean_name = sanitize_fund_name(item.fund_name)
        existing = item.fund_code
        if is_provisional_fund_code(existing):
            existing = None
        code, _ = resolve_holding_fund_code(clean_name, existing_code=existing)
        updates: dict = {}
        if clean_name and clean_name != item.fund_name:
            updates["fund_name"] = clean_name
        if code and code != item.fund_code:
            updates["fund_code"] = code
        reconciled.append(item.model_copy(update=updates) if updates else item)
    return reconciled
