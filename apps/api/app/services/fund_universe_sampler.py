"""基金研究池去重与分层抽样。

设计文档：docs/superpowers/specs/2026-06-24-factor-style-and-universe-design.md（3D）。

纯函数。把「取前 N 名」（偏强样本）换成「跨业绩段等距抽样」，让横截面更中性。
诚实边界：榜单本身仍有幸存者偏差（清盘基金不在榜），彻底去偏需 point-in-time 库。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict


FUND_TYPE_ORDER = ("gp", "hh", "zq", "zs", "qdii", "fof", "unknown")
_SHARE_CLASS_SUFFIX = re.compile(r"(?:[A-Z]类|[A-Z])$", re.IGNORECASE)


def canonical_portfolio_name(name: str) -> str:
    """保守移除常见 A/C/E 等份额后缀，得到底层组合近似键。"""
    normalized = re.sub(r"\s+", "", str(name or "").strip())
    return _SHARE_CLASS_SUFFIX.sub("", normalized)


def _share_class_priority(row: dict) -> tuple[int, int, str, str]:
    name = re.sub(r"\s+", "", str(row.get("fund_name") or ""))
    suffix = name[-1:].upper()
    # A 份额通常历史最长，优先用于长窗口研究；其余保持确定性。
    priority = 0 if suffix == "A" else 1
    rank_priority = 0 if row.get("rank_enriched") is True else 1
    established = str(row.get("established_date") or "9999-12-31")
    return rank_priority, priority, established, str(row.get("fund_code") or "")


def _stable_row_token(row: dict) -> str:
    material = "\n".join(
        (
            str(row.get("fund_type") or "unknown").lower(),
            canonical_portfolio_name(str(row.get("fund_name") or "")),
            str(row.get("fund_code") or ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _performance_order(row: dict) -> tuple[int, float, str]:
    raw_value = row.get("return_1y_percent")
    try:
        value = float(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        value = None
    if value is None or not math.isfinite(value):
        return 1, 0.0, _stable_row_token(row)
    return 0, -value, _stable_row_token(row)


def dedupe_share_classes(rank_rows: list[dict]) -> list[dict]:
    """同类别、同规范名只保留一个代表份额，避免一个组合重复影响 IC。"""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rank_rows:
        code = str(row.get("fund_code") or "").strip()
        name = canonical_portfolio_name(str(row.get("fund_name") or ""))
        if not code or not name:
            continue
        fund_type = str(row.get("fund_type") or "unknown").lower()
        grouped[(fund_type, name)].append(row)
    return [
        dict(min(rows, key=_share_class_priority))
        for _, rows in sorted(grouped.items(), key=lambda item: item[0])
    ]


def _allocate_strata(counts: dict[str, int], sample_size: int) -> dict[str, int]:
    available = {key: count for key, count in counts.items() if count > 0}
    if not available or sample_size <= 0:
        return {key: 0 for key in available}
    target = min(sample_size, sum(available.values()))
    minimum = min(80, max(1, target // len(available)))
    allocation = {key: min(count, minimum) for key, count in available.items()}
    remaining = target - sum(allocation.values())
    while remaining > 0:
        candidates = [
            key for key, count in available.items() if allocation[key] < count
        ]
        if not candidates:
            break
        total_capacity = sum(available[key] - allocation[key] for key in candidates)
        progressed = False
        for key in candidates:
            capacity = available[key] - allocation[key]
            add = min(capacity, max(1, round(remaining * capacity / total_capacity)))
            add = min(add, remaining)
            allocation[key] += add
            remaining -= add
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break
    return allocation


def stratified_sample_universe(rank_rows: list[dict], sample_size: int) -> list[dict]:
    """份额去重后按基金类别分配名额，并在类别内跨业绩分位等距抽样。"""
    deduped = dedupe_share_classes(rank_rows)
    if sample_size <= 0 or len(deduped) <= sample_size:
        return deduped
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in deduped:
        by_type[str(row.get("fund_type") or "unknown").lower()].append(row)
    allocation = _allocate_strata(
        {key: len(rows) for key, rows in by_type.items()}, sample_size
    )
    sampled: list[dict] = []
    ordered_types = [key for key in FUND_TYPE_ORDER if key in by_type]
    ordered_types.extend(sorted(set(by_type) - set(ordered_types)))
    for fund_type in ordered_types:
        target = allocation.get(fund_type, 0)
        current_rows = [
            row for row in by_type[fund_type] if row.get("rank_enriched") is True
        ]
        # A complete single-request rank snapshot identifies current funds.  If
        # it is absent, hash ordering gives a stable, input-order-independent
        # fallback instead of silently biasing the sample toward low codes.
        candidates = current_rows if len(current_rows) >= target else by_type[fund_type]
        sampled.extend(sample_universe(sorted(candidates, key=_performance_order), target))
    return sampled[:sample_size]


def universe_coverage(rank_rows: list[dict], sampled_rows: list[dict]) -> dict:
    deduped = dedupe_share_classes(rank_rows)
    source_by_type: dict[str, int] = defaultdict(int)
    unique_by_type: dict[str, int] = defaultdict(int)
    sampled_by_type: dict[str, int] = defaultdict(int)
    rank_enriched_count = 0
    for row in rank_rows:
        source_by_type[str(row.get("fund_type") or "unknown").lower()] += 1
        if row.get("rank_enriched") is True:
            rank_enriched_count += 1
    for row in deduped:
        unique_by_type[str(row.get("fund_type") or "unknown").lower()] += 1
    for row in sampled_rows:
        sampled_by_type[str(row.get("fund_type") or "unknown").lower()] += 1
    return {
        "source_share_classes": len(rank_rows),
        "unique_portfolios": len(deduped),
        "sampled_portfolios": len(sampled_rows),
        "source_by_type": dict(source_by_type),
        "unique_by_type": dict(unique_by_type),
        "sampled_by_type": dict(sampled_by_type),
        "rank_enriched_share_classes": rank_enriched_count,
        "rank_enrichment_rate": (
            round(rank_enriched_count / len(rank_rows), 4) if rank_rows else 0.0
        ),
    }


def sample_universe(rank_rows: list[dict], sample_size: int) -> list[dict]:
    """在按业绩排序的榜单里等距分层抽样。

    rank_rows 数 <= sample_size 或 sample_size <= 0 时原样返回。
    否则以 step = n / sample_size 等距取样，覆盖从榜首到榜尾各业绩段。
    """
    n = len(rank_rows)
    if sample_size <= 0 or n <= sample_size:
        return list(rank_rows)
    step = n / sample_size
    return [rank_rows[int(i * step)] for i in range(sample_size)]
