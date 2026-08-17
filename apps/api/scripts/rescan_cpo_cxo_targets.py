"""一次性运维脚本：预筛可能命中 CPO/CXO 新细分规则的基金并触发重算。

用法（在 apps/api 目录下）：
    python scripts/rescan_cpo_cxo_targets.py screen   # 只预筛，打印目标列表
    python scripts/rescan_cpo_cxo_targets.py run      # 预筛 + 置过期 + 同步重算
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "app.db"

_RULES = {
    "CPO": {"parent": "通信设备", "board": "BK1128"},
    "CXO": {"parent": "医疗服务", "board": "BK1600"},
}
_MIN_MATCHED_STOCKS = 2
_MIN_MATCHED_WEIGHT_RATIO = 0.60


def _board_members() -> dict[str, set[str]]:
    from app.services.stock_classification_evidence import (
        fetch_current_board_constituent_evidence,
    )

    evidence = fetch_current_board_constituent_evidence(
        [rule["board"] for rule in _RULES.values()],
    )
    members: dict[str, set[str]] = {}
    for theme, rule in _RULES.items():
        raw = evidence.get(rule["board"]) or {}
        members[theme] = {
            str(code).strip() for code in raw.get("codes") or [] if str(code).strip()
        }
        print(f"{theme} board {rule['board']}: {len(members[theme])} constituents")
    return members


def _screen(members: dict[str, set[str]]) -> dict[str, list[str]]:
    """返回 {原板块行 fund_code: [可能的新主题]}，含 verified 与 pending。"""

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT fund_code, sector_name, identity_status, detail
        FROM fund_sector_current
        WHERE is_primary = 1
          AND sector_name IN ('医疗', '通信技术')
          AND source IN ('precompute_holdings', 'holdings_infer')
        """
    ).fetchall()
    conn.close()

    hits: dict[str, list[str]] = {}
    scanned = 0
    for row in rows:
        scanned += 1
        try:
            detail = json.loads(row["detail"] or "{}")
        except (TypeError, ValueError):
            continue
        evidence = detail.get("evidence") or []
        if not isinstance(evidence, list):
            continue
        for theme, rule in _RULES.items():
            candidates = [
                (str(item.get("stock_code") or ""), float(item.get("weight") or 0.0))
                for item in evidence
                if isinstance(item, dict)
                and str(item.get("industry") or "") == rule["parent"]
                and float(item.get("weight") or 0.0) > 0
            ]
            if len(candidates) < _MIN_MATCHED_STOCKS:
                continue
            matched = [
                (code, weight)
                for code, weight in candidates
                if code in members[theme]
            ]
            candidate_mass = sum(weight for _code, weight in candidates)
            matched_mass = sum(weight for _code, weight in matched)
            if (
                len(matched) >= _MIN_MATCHED_STOCKS
                and candidate_mass > 0
                and matched_mass / candidate_mass >= _MIN_MATCHED_WEIGHT_RATIO
            ):
                hits.setdefault(row["fund_code"], []).append(theme)
    print(f"scanned {scanned} primary rows, {len(hits)} funds may switch theme")
    return hits


def _expire_verified_rows(fund_codes: list[str]) -> int:
    """把目标基金的 fresh verified 持仓主行置为过期，跑批才不会被 skip。"""

    if not fund_codes:
        return 0
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(fund_codes))
    cursor = conn.execute(
        f"""
        UPDATE fund_sector_current
        SET expires_at = ?
        WHERE fund_code IN ({placeholders})
          AND source IN ('precompute_holdings', 'holdings_infer')
          AND identity_status = 'verified'
        """,
        (past, *fund_codes),
    )
    conn.commit()
    expired = cursor.rowcount
    conn.close()
    return expired


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "screen"
    members = _board_members()
    hits = _screen(members)
    for code, themes in sorted(hits.items()):
        print(f"  {code} -> {'+'.join(themes)}")
    if action != "run":
        return

    codes = sorted(hits)
    expired = _expire_verified_rows(codes)
    print(f"expired {expired} verified rows for {len(codes)} funds")

    from app.services.fund_primary_sector_precompute import run_precompute_batch

    result = run_precompute_batch(
        limit=len(codes),
        mode="holdings",
        force=True,
        fund_codes=codes,
        sleep_seconds=0.0,
    )
    print("batch result:", json.dumps(result.to_dict(), ensure_ascii=False))


if __name__ == "__main__":
    main()
