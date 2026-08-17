"""诊断脚本：逐板块打印候选基金卡在哪道硬门槛上。

用法（apps/api 或容器 /app 下）：
    python scripts/diagnose_sector_vehicles.py [--strategy opportunity_first] [--user 5] CXO CPO 贵金属
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    args = sys.argv[1:]
    strategy = "risk_first"
    user_id = 5
    if "--strategy" in args:
        index = args.index("--strategy")
        strategy = args[index + 1]
        del args[index : index + 2]
    if "--user" in args:
        index = args.index("--user")
        user_id = int(args[index + 1])
        del args[index : index + 2]
    sectors = args or ["CXO", "CPO", "贵金属", "算力租赁"]
    from app.request_context import set_request_user_id
    from app.services.discovery_candidate_pool import (
        build_candidate_pool,
        enrich_candidates,
    )
    from app.services.discovery_recommendation_scope import _fund_gate_reasons

    set_request_user_id(user_id)
    print(f"discovery_strategy={strategy}")

    pool = build_candidate_pool(sectors, discovery_strategy=strategy)
    print(f"recalled {len(pool)} candidates")
    by_sector: dict[str, list[dict]] = {}
    for item in pool:
        by_sector.setdefault(str(item.get("sector_label")), []).append(item)
    for sector in sectors:
        rows = by_sector.get(sector, [])
        print(f"  {sector}: {len(rows)} recalled")

    enriched = enrich_candidates(pool, discovery_strategy=strategy)
    print("--- gate evaluation ---")
    by_sector.clear()
    for item in enriched:
        by_sector.setdefault(str(item.get("sector_label")), []).append(item)
    for sector in sectors:
        rows = by_sector.get(sector, [])
        passed = [r for r in rows if not _fund_gate_reasons(r)]
        print(f"[{sector}] {len(rows)} candidates, {len(passed)} pass all fund gates")
        for row in rows[:8]:
            gate = row.get("quality_gate") or {}
            reasons = _fund_gate_reasons(row)
            print(
                " ",
                row.get("fund_code"),
                str(row.get("fund_name"))[:22],
                "| gates:",
                ",".join(reasons) or "PASS",
            )
            detail = {
                "quality": f"{gate.get('status')} {gate.get('reasons') or []}",
                "vehicle": (
                    f"{row.get('vehicle_quality_status')} "
                    f"{row.get('vehicle_quality_score')} "
                    f"{(row.get('vehicle_quality_assessment') or {}).get('penalties') or []}"
                ),
                "identity": f"{row.get('sector_match_kind')} verified={row.get('sector_mapping_verified')}",
            }
            print("   ", json.dumps(detail, ensure_ascii=False))


if __name__ == "__main__":
    main()
