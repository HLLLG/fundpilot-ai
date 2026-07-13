from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass

from app.services.fund_name_fuzzy import (
    FUZZY_SEARCH_MIN_SCORE,
    best_fuzzy_fund_match,
    fuzzy_name_match_score,
    fuzzy_search_funds,
)
from app.services.fund_name_table_store import (
    clear_persisted_fund_name_table_cache,
    load_cached_fund_name_table,
    save_fund_name_table_cache,
)
from app.services.fund_name_utils import (
    extract_share_class_letter,
    is_fund_name_match,
    lookup_match_score,
    normalize_fund_name_for_lookup,
)

_SUBPROCESS_TIMEOUT = 120

UNRESOLVED_FUND_CODE_HINT = (
    "未在东财基金库匹配到代码，请点「搜索」手动选取正确基金，确认后再入库。"
)

_TABLE_SENTINEL_CHECKS: tuple[tuple[str, str], ...] = (
    ("000001", "华夏"),
    ("026790", "中欧"),
)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _fund_name_looks_valid(name: str) -> bool:
    if not name or "\ufffd" in name:
        return False
    cjk_count = sum(1 for ch in name if "\u4e00" <= ch <= "\u9fff")
    return cjk_count >= 2


def _fund_name_table_looks_valid(table: list[tuple[str, str]]) -> bool:
    if not table:
        return False
    if not all(_fund_name_looks_valid(name) for _, name in table[: min(20, len(table))]):
        return False
    if len(table) < 1000:
        return True
    by_code = dict(table)
    return all(
        needle in by_code.get(code, "")
        for code, needle in _TABLE_SENTINEL_CHECKS
        if code in by_code
    )


_fund_name_table_cache: list[tuple[str, str]] | None = None
_fund_name_index_cache: _FundNameIndex | None = None


@dataclass(frozen=True)
class _FundNameIndex:
    """东财基金名称表内存索引：by_code、归一化名、名称/bigram 子串搜索。"""

    source_table: list[tuple[str, str]]
    table: tuple[tuple[str, str], ...]
    by_code: dict[str, str]
    by_normalized: dict[str, tuple[tuple[str, str], ...]]
    postings_by_name_bigram: dict[str, frozenset[str]]
    postings_by_norm_bigram: dict[str, frozenset[str]]


def _text_bigrams(text: str) -> tuple[str, ...]:
    if len(text) < 2:
        return ()
    return tuple(text[index : index + 2] for index in range(len(text) - 1))


def _add_bigram_postings(
    target: dict[str, set[str]],
    text: str,
    code: str,
) -> None:
    for bigram in set(_text_bigrams(text)):
        target[bigram].add(code)


def _candidate_codes_by_bigrams(
    postings: dict[str, frozenset[str]],
    grams: tuple[str, ...],
) -> set[str]:
    if not grams:
        return set()
    candidate_codes: set[str] | None = None
    for gram in grams:
        codes = postings.get(gram)
        if not codes:
            return set()
        candidate_codes = codes if candidate_codes is None else candidate_codes & codes
    return candidate_codes or set()


def _build_fund_name_index(table: list[tuple[str, str]]) -> _FundNameIndex:
    by_code: dict[str, str] = {}
    by_normalized_lists: dict[str, list[tuple[str, str]]] = defaultdict(list)
    name_bigram_sets: dict[str, set[str]] = defaultdict(set)
    norm_bigram_sets: dict[str, set[str]] = defaultdict(set)
    for code, name in table:
        by_code[code] = name
        normalized = normalize_fund_name_for_lookup(name)
        if normalized:
            by_normalized_lists[normalized].append((code, name))
            _add_bigram_postings(norm_bigram_sets, normalized, code)
        _add_bigram_postings(name_bigram_sets, name, code)
    by_normalized = {
        key: tuple(rows) for key, rows in by_normalized_lists.items()
    }
    return _FundNameIndex(
        source_table=table,
        table=tuple(table),
        by_code=by_code,
        by_normalized=by_normalized,
        postings_by_name_bigram={
            key: frozenset(codes) for key, codes in name_bigram_sets.items()
        },
        postings_by_norm_bigram={
            key: frozenset(codes) for key, codes in norm_bigram_sets.items()
        },
    )


def _fund_name_index() -> _FundNameIndex:
    global _fund_name_index_cache
    table = _fund_name_table()
    if _fund_name_index_cache is None or _fund_name_index_cache.source_table is not table:
        _fund_name_index_cache = _build_fund_name_index(table)
    return _fund_name_index_cache


def clear_fund_name_table_cache() -> None:
    global _fund_name_table_cache, _fund_name_index_cache
    _fund_name_table_cache = None
    _fund_name_index_cache = None


def clear_all_fund_name_table_caches() -> None:
    clear_fund_name_table_cache()
    clear_persisted_fund_name_table_cache()


def preload_fund_name_table() -> None:
    """启动时预热基金名称表及索引，避免首次 OCR 查码承担索引构建开销。"""
    try:
        _fund_name_index()
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
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
            env=_subprocess_env(),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, list):
            return None
        return [(str(code), str(name)) for code, name in payload if code and name]
    except Exception:
        return None


def _fund_name_table() -> list[tuple[str, str]]:
    global _fund_name_table_cache
    if _fund_name_table_cache is not None:
        return _fund_name_table_cache

    cached = load_cached_fund_name_table()
    if cached and _fund_name_table_looks_valid(cached):
        _fund_name_table_cache = cached
        return cached

    fetched: list[tuple[str, str]] | None = None
    for _ in range(2):
        candidate = _fetch_fund_name_table_subprocess()
        if candidate and _fund_name_table_looks_valid(candidate):
            save_fund_name_table_cache(candidate)
            _fund_name_table_cache = candidate
            return candidate
        fetched = candidate

    _fund_name_table_cache = fetched or []
    return _fund_name_table_cache


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

    looked_up, match_source = lookup_fund_code_by_name(fund_name)
    if looked_up:
        return looked_up, match_source
    if existing_code and existing_code != "000000" and not is_provisional_fund_code(existing_code):
        return existing_code, "profile"
    return None, None


def lookup_fund_code_by_name(fund_name: str) -> tuple[str | None, str | None]:
    """按名称查码。返回 (fund_code, source)；source 为 akshare / fuzzy。"""
    target = normalize_fund_name_for_lookup(fund_name)
    if not target:
        return None, None

    target_class = extract_share_class_letter(fund_name)
    index = _fund_name_index()
    table = list(index.table)

    exact_rows = index.by_normalized.get(target)
    if exact_rows:
        if len(exact_rows) == 1:
            return exact_rows[0][0], "akshare"
        if target_class:
            for code, name in exact_rows:
                if extract_share_class_letter(name) == target_class:
                    return code, "akshare"
        return exact_rows[0][0], "akshare"

    candidates: list[tuple[int, str]] = []
    for code, name in table:
        normalized = normalize_fund_name_for_lookup(name)
        if not normalized or not is_fund_name_match(target, normalized):
            continue
        table_class = extract_share_class_letter(name)
        if target_class and table_class and target_class != table_class:
            continue
        score = lookup_match_score(target, normalized)
        if score > 0:
            candidates.append((score, code))

    if candidates:
        if target_class is None:
            class_by_code = {code: extract_share_class_letter(name) for code, name in table}
            c_only = [item for item in candidates if class_by_code.get(item[1]) == "C"]
            if len(c_only) == 1 and len(candidates) >= 2:
                return c_only[0][1], "akshare"

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score = candidates[0][0]
        top = [code for score, code in candidates if score == best_score]
        if len(top) == 1:
            return top[0], "akshare"

    fuzzy = best_fuzzy_fund_match(fund_name, table)
    if fuzzy:
        return fuzzy[0], "fuzzy"
    return None, None


def _reload_fund_name_table() -> list[tuple[str, str]]:
    clear_fund_name_table_cache()
    return _fund_name_table()


def lookup_fund_name_by_code(fund_code: str) -> str | None:
    code = fund_code.strip().zfill(6)
    if len(code) != 6 or not code.isdigit():
        return None
    index = _fund_name_index()
    name = index.by_code.get(code)
    if name and _fund_name_looks_valid(name):
        return name
    for table_code, table_name in _reload_fund_name_table():
        if table_code == code:
            return table_name if _fund_name_looks_valid(table_name) else None
    return None


def search_funds_by_keyword(keyword: str, *, limit: int = 12) -> list[dict[str, str]]:
    """东财基金表模糊搜索，供确认页手动选码（养基宝式核对）。"""
    query = keyword.strip()
    if not query:
        return []

    index = _fund_name_index()
    table_snapshot = index.table
    results: list[tuple[int, str, str]] = []

    if query.isdigit() and len(query) <= 6:
        code_query = query.zfill(6)
        name = index.by_code.get(code_query)
        if name and _fund_name_looks_valid(name):
            return [{"fund_code": code_query, "fund_name": name}]
        for table_code, table_name in _reload_fund_name_table():
            if table_code == code_query and _fund_name_looks_valid(table_name):
                return [{"fund_code": code_query, "fund_name": table_name}]
        # reload 失败时会清空内存表；前缀匹配仍用 reload 前的快照（与原实现一致）
        prefix_source = table_snapshot
        index = _fund_name_index()
        if index.table:
            prefix_source = index.table
        for code, table_name in prefix_source:
            if code.startswith(query) and _fund_name_looks_valid(table_name):
                results.append((900_000 + len(query), code, table_name))

    index = _fund_name_index()
    query_norm = normalize_fund_name_for_lookup(query)
    candidate_codes = set()
    if len(query) >= 2:
        candidate_codes |= _candidate_codes_by_bigrams(
            index.postings_by_name_bigram,
            _text_bigrams(query),
        )
    if query_norm and len(query_norm) >= 2:
        candidate_codes |= _candidate_codes_by_bigrams(
            index.postings_by_norm_bigram,
            _text_bigrams(query_norm),
        )

    if candidate_codes:
        iterable = (
            (code, index.by_code[code])
            for code in candidate_codes
            if code in index.by_code
        )
    else:
        iterable = index.table if index.table else table_snapshot

    for code, name in iterable:
        if query in name:
            score = 500_000 + len(query)
        elif query_norm:
            score = lookup_match_score(query_norm, normalize_fund_name_for_lookup(name))
        else:
            score = 0
        if score > 0:
            results.append((score, code, name))

    if not results and query_norm and len(query_norm) >= 4:
        fuzzy_table = list(index.table) if index.table else list(table_snapshot)
        for score, code, name in fuzzy_search_funds(
            query,
            fuzzy_table,
            limit=limit,
            min_score=FUZZY_SEARCH_MIN_SCORE,
        ):
            fuzzy_boost = int(fuzzy_name_match_score(query, name) * 400_000)
            results.append((fuzzy_boost, code, name))

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
