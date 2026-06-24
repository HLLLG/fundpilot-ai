from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.models import Holding
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_labels import normalize_sector_label
from app.services.sector_signal_backtest import build_sector_signal_backtest
from app.services.sector_signal_rules import rule_label

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 3600


def sector_labels_from_holdings(holdings: list[Holding]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for holding in holdings:
        raw = normalize_sector_label(holding.sector_name)
        if not raw:
            continue
        canon = get_canonical_sector(raw)
        if canon is None:
            continue
        if canon.label in seen:
            continue
        seen.add(canon.label)
        labels.append(canon.label)
    return labels


def build_signal_backtest_context(
    sector_labels: list[str] | None = None,
    *,
    lookback_days: int | None = None,
    fetch_series=None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.sector_signal_backtest_enabled:
        return {
            "enabled": False,
            "has_data": False,
            "message": "板块信号回测已关闭（FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED=false）。",
        }

    labels = sector_labels or []
    window = lookback_days or settings.sector_signal_backtest_days
    cache_key = f"{','.join(sorted(labels))}:{window}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    full = build_sector_signal_backtest(
        labels or None,
        lookback_days=window,
        fetch_series=fetch_series,
    )
    compact = _compact_backtest_context(full, window)
    _CACHE[cache_key] = (now, compact)
    return compact


def signal_backtest_for_sector(
    sector_name: str | None,
    context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not context or not context.get("has_data"):
        return None
    label = normalize_sector_label(sector_name)
    if not label:
        return None
    canon = get_canonical_sector(label)
    if canon is None:
        return None
    for entry in context.get("sectors") or []:
        if entry.get("sector_label") == canon.label:
            return entry
    return None


def _compact_backtest_context(full: dict[str, Any], window: int) -> dict[str, Any]:
    by_rule = _compact_rules(full.get("by_rule") or {})
    sectors = []
    for entry in full.get("sectors") or []:
        if not entry.get("resolved"):
            continue
        sectors.append(
            {
                "sector_label": entry.get("sector_label"),
                "sample_days": entry.get("sample_days"),
                "by_rule": _compact_rules(entry.get("by_rule") or {}),
            }
        )

    return {
        "enabled": True,
        "has_data": bool(full.get("has_data")),
        "lookback_days": window,
        "sector_count": full.get("sector_count", 0),
        "by_rule": by_rule,
        "sectors": sectors,
        "summary_lines": list(full.get("summary_lines") or []),
        "message": full.get("message"),
    }


def _compact_rules(raw: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    compact: dict[str, dict[str, Any]] = {}
    for rule_id, bucket in raw.items():
        triggers = int(bucket.get("trigger_count") or 0)
        if triggers <= 0:
            continue
        compact[rule_id] = {
            "rule_id": rule_id,
            "label": bucket.get("label") or rule_label(rule_id),
            "trigger_count": triggers,
            "hit_count": int(bucket.get("hit_count") or 0),
            "hit_rate_percent": bucket.get("hit_rate_percent"),
            "baseline_rate_percent": bucket.get("baseline_rate_percent"),
            "edge_percent": bucket.get("edge_percent"),
            "significant": bucket.get("significant"),
            "beats_baseline": bucket.get("beats_baseline"),
            "beats_random": bucket.get("beats_random"),
        }
    return compact
