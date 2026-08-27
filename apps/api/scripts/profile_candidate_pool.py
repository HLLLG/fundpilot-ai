"""拆开 build_candidate_pool 计时：召回扫目录 vs 同类分位。

用法：
    cd apps/api && ./.venv/Scripts/python.exe scripts/profile_candidate_pool.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.request_context import set_request_user_id
from app.services import discovery_candidate_pool as pool_mod
from app.services.fund_discovery_data_cache import fetch_discovery_fund_universe_cached

TARGET_SECTORS = [
    "半导体",
    "白酒",
    "商业航天",
    "人工智能",
    "新能源车",
    "医药",
    "有色金属",
    "国防军工",
    "光伏",
    "机器人",
    "贵金属",
    "证券",
    "云计算",
    "金融科技",
]


def _wrap(owner, name: str):
    original = getattr(owner, name)
    stats = {"calls": 0, "seconds": 0.0}

    def wrapped(*args, **kwargs):
        started = time.monotonic()
        try:
            return original(*args, **kwargs)
        finally:
            stats["calls"] += 1
            stats["seconds"] += time.monotonic() - started

    wrapped.__wrapped__ = original
    setattr(owner, name, wrapped)
    return stats


def main() -> None:
    set_request_user_id(1)
    t0 = time.monotonic()
    universe = fetch_discovery_fund_universe_cached(limit=20_000) or []
    print(f"universe  {time.monotonic() - t0:6.2f}s  rows={len(universe)}")

    timers = {
        "list_fund_primary_sectors": _wrap(pool_mod, "list_fund_primary_sectors"),
        "list_fund_primary_sectors_by_sector_names": _wrap(
            pool_mod, "list_fund_primary_sectors_by_sector_names"
        ),
        "_candidates_for_sector": _wrap(pool_mod, "_candidates_for_sector"),
        "_verified_primary_sectors_by_code": _wrap(
            pool_mod, "_verified_primary_sectors_by_code"
        ),
        "attach_descriptive_peer_research": _wrap(
            pool_mod, "attach_descriptive_peer_research"
        ),
        "_attach_descriptive_peer_research": _wrap(
            pool_mod, "_attach_descriptive_peer_research"
        ),
        "_is_execution_verified_primary_mapping": _wrap(
            pool_mod, "_is_execution_verified_primary_mapping"
        ),
        "build_peer_rank": _wrap(pool_mod, "build_peer_rank"),
        "_resolve_fund_name": _wrap(pool_mod, "_resolve_fund_name"),
        "annotate_candidate_sector_identity": _wrap(
            pool_mod, "annotate_candidate_sector_identity"
        ),
    }

    recall: dict = {}
    decision_at = datetime.now(timezone.utc)
    started = time.monotonic()
    selected = pool_mod.build_candidate_pool(
        TARGET_SECTORS,
        exclude_codes={"519674", "015945", "161725"},
        fund_type_preference="any",
        selection_strategy="balanced",
        discovery_strategy="opportunity_first",
        prepared_universe_rows=universe,
        per_sector=6,
        pool_cap=54,
        sector_opportunities=[
            {"sector_label": label, "selection_priority_score": 80.0}
            for label in TARGET_SECTORS
        ],
        decision_at=decision_at,
        recall_audit_sink=recall,
    )
    elapsed = time.monotonic() - started
    alt_n = sum(
        len(item.get("_share_family_alternatives") or [])
        for item in selected
        if isinstance(item, dict)
    )
    print(f"build_candidate_pool  {elapsed:6.2f}s  families={len(selected)}  alts={alt_n}")
    finalists = selected[:34]
    attach_started = time.monotonic()
    pool_mod.attach_descriptive_peer_research(
        finalists,
        universe=universe,
        decision_at=decision_at,
    )
    print(
        f"attach_peer_finalists  {time.monotonic() - attach_started:6.2f}s  "
        f"n={len(finalists)}"
    )
    print(
        f"recall  total={recall.get('scope', {}).get('candidate_count_total')}  "
        f"retained={recall.get('scope', {}).get('candidate_count_retained')}"
    )
    print()
    for name, stats in timers.items():
        print(f"  {name:44s}  {stats['seconds']:7.2f}s  calls={stats['calls']}")


if __name__ == "__main__":
    main()
