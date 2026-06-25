from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.services.fund_name_utils import (
    FUND_ISSUER_PREFIX,
    extract_share_class_letter,
    is_fund_name_match,
    lookup_match_score,
    normalize_fund_name_for_lookup,
)

FUZZY_AUTO_MATCH_MIN_SCORE = 0.86
FUZZY_SEARCH_MIN_SCORE = 0.72

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def detect_fund_issuer(name: str) -> str | None:
    normalized = normalize_fund_name_for_lookup(name)
    for issuer in sorted(FUND_ISSUER_PREFIX, key=len, reverse=True):
        if normalized.startswith(issuer):
            return issuer
    return None


def lookup_core_tokens(name: str) -> set[str]:
    normalized = normalize_fund_name_for_lookup(name)
    core = re.sub(
        r"(?:混合|联接|ETF联接|ETF联|指数|股票)"
        r"(?:[（(](?:QDII|LOF|FOF|QDII-ETF)[)）])?"
        r"(?:人民币|美元|港币)?"
        r"[A-CEH]$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    tokens = {match.group() for match in _TOKEN_RE.finditer(core)}
    issuer = detect_fund_issuer(normalized)
    if issuer:
        tokens.add(issuer)
    for hint in ("ETF", "联接", "指数", "混合", "股票"):
        if hint in normalized:
            tokens.add(hint)
    return {token for token in tokens if len(token) >= 2}


def _fuzzy_relax_name(name: str) -> str:
    """模糊比对专用：在 lookup 归一化之后再放宽板块/市场写法差异。"""
    result = name.replace("上证", "").replace("中证", "")
    return result.replace("科创板", "科创")


def fuzzy_name_match_score(left: str, right: str) -> float:
    left_norm = normalize_fund_name_for_lookup(left)
    right_norm = normalize_fund_name_for_lookup(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm or is_fund_name_match(left_norm, right_norm):
        return 1.0

    left_relaxed = _fuzzy_relax_name(left_norm)
    right_relaxed = _fuzzy_relax_name(right_norm)
    if left_relaxed == right_relaxed:
        return 1.0

    ratio = SequenceMatcher(None, left_relaxed, right_relaxed).ratio()
    left_tokens = lookup_core_tokens(left_norm)
    right_tokens = lookup_core_tokens(right_norm)
    if not left_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens) / len(left_tokens)
    issuer_left = detect_fund_issuer(left_norm)
    issuer_right = detect_fund_issuer(right_norm)
    if issuer_left and issuer_right and issuer_left != issuer_right:
        return 0.0

    substring_bonus = 0.0
    if lookup_match_score(left_norm, right_norm) > 0:
        substring_bonus = 0.08

    return min(1.0, ratio * 0.55 + overlap * 0.45 + substring_bonus)


def iter_fuzzy_candidates(
    query: str,
    table: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    issuer = detect_fund_issuer(query)
    if issuer:
        scoped = [(code, name) for code, name in table if name.startswith(issuer)]
        if scoped:
            return scoped
    query_tokens = lookup_core_tokens(query)
    if not query_tokens:
        return table
    filtered: list[tuple[str, str]] = []
    for code, name in table:
        if query_tokens & lookup_core_tokens(name):
            filtered.append((code, name))
    return filtered or table


def best_fuzzy_fund_match(
    query: str,
    table: list[tuple[str, str]],
    *,
    min_score: float = FUZZY_AUTO_MATCH_MIN_SCORE,
) -> tuple[str, str, float] | None:
    target_class = extract_share_class_letter(query)
    best: tuple[str, str, float] | None = None
    for code, name in iter_fuzzy_candidates(query, table):
        table_class = extract_share_class_letter(name)
        if target_class and table_class and target_class != table_class:
            continue
        score = fuzzy_name_match_score(query, name)
        if score < min_score:
            continue
        if best is None or score > best[2]:
            best = (code, name, score)
    if best is None:
        return None
    # 拒绝模糊度接近的第二名，避免误匹配
    second_best = 0.0
    for code, name in iter_fuzzy_candidates(query, table):
        if code == best[0]:
            continue
        table_class = extract_share_class_letter(name)
        if target_class and table_class and target_class != table_class:
            continue
        second_best = max(second_best, fuzzy_name_match_score(query, name))
    if second_best and best[2] - second_best < 0.04:
        return None
    return best


def fuzzy_search_funds(
    query: str,
    table: list[tuple[str, str]],
    *,
    limit: int = 12,
    min_score: float = FUZZY_SEARCH_MIN_SCORE,
) -> list[tuple[int, str, str]]:
    scored: list[tuple[float, str, str]] = []
    for code, name in iter_fuzzy_candidates(query, table):
        score = fuzzy_name_match_score(query, name)
        if score >= min_score:
            scored.append((score, code, name))
    scored.sort(key=lambda item: item[0], reverse=True)
    results: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for score, code, name in scored:
        if code in seen:
            continue
        seen.add(code)
        results.append((int(score * 1_000_000), code, name))
        if len(results) >= limit:
            break
    return results
