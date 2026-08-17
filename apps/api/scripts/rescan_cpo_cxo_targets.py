"""一次性运维脚本：预筛可能命中 CPO/CXO/算力租赁 细分规则的基金并触发重算。

通过 ``app.db_connect`` 的统一连接层访问数据库，本地 SQLite 与生产 MySQL
都可直接运行。

用法（apps/api 或容器 /app 下）：
    python scripts/rescan_cpo_cxo_targets.py screen   # 只预筛，打印目标列表
    python scripts/rescan_cpo_cxo_targets.py run      # 预筛 + 置过期 + 同步重算
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 细分主题 → (旧的宽板块主行, parent f127 行业, 概念/行业板)。预筛只扫当前主行
# 落在旧宽板块上的基金，因为只有它们可能被对应规则改写。
_RULES = {
    "CPO": {"old_sectors": ("通信技术",), "parent": ("通信设备",), "board": "BK1128"},
    "CXO": {"old_sectors": ("医疗",), "parent": ("医疗服务",), "board": "BK1600"},
    "算力租赁": {
        "old_sectors": ("计算机", "通信技术", "软件", "互联网"),
        "parent": ("IT服务Ⅱ", "通信服务"),
        "board": "BK1134",
    },
}
_MIN_MATCHED_STOCKS = 2
_MIN_MATCHED_WEIGHT_RATIO = 0.60


def _db_rows(sql: str, params: tuple = ()) -> list[dict]:
    from app.database import _connect

    with _connect() as connection:
        cursor = connection.execute(sql, params)
        # MySQL 路径返回 DictCursor 行（dict），SQLite 路径返回 sqlite3.Row。
        return [
            row if isinstance(row, dict) else dict(row)
            for row in cursor.fetchall()
        ]


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
    """返回 {fund_code: [可能的新主题]}，含 verified 与 pending 主行。"""

    old_sectors = sorted(
        {sector for rule in _RULES.values() for sector in rule["old_sectors"]}
    )
    placeholders = ",".join("?" * len(old_sectors))
    rows = _db_rows(
        f"""
        SELECT fund_code, sector_name, identity_status, detail
        FROM fund_sector_current
        WHERE is_primary = 1
          AND sector_name IN ({placeholders})
          AND source IN ('precompute_holdings', 'holdings_infer')
        """,
        tuple(old_sectors),
    )

    hits: dict[str, list[str]] = {}
    for row in rows:
        try:
            detail = json.loads(row.get("detail") or "{}")
        except (TypeError, ValueError):
            continue
        evidence = detail.get("evidence") or []
        if not isinstance(evidence, list):
            continue
        for theme, rule in _RULES.items():
            if str(row.get("sector_name") or "") not in rule["old_sectors"]:
                continue
            candidates = [
                (str(item.get("stock_code") or ""), float(item.get("weight") or 0.0))
                for item in evidence
                if isinstance(item, dict)
                and str(item.get("industry") or "") in rule["parent"]
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
                themes = hits.setdefault(str(row.get("fund_code") or ""), [])
                if theme not in themes:
                    themes.append(theme)
    print(f"scanned {len(rows)} primary rows, {len(hits)} funds may switch theme")
    return hits


def _expire_verified_rows(fund_codes: list[str]) -> int:
    """把目标基金的 fresh verified 持仓主行置为过期，跑批才不会被 skip。"""

    if not fund_codes:
        return 0
    from app.database import _connect

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    placeholders = ",".join("?" * len(fund_codes))
    with _connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE fund_sector_current
            SET expires_at = ?
            WHERE fund_code IN ({placeholders})
              AND source IN ('precompute_holdings', 'holdings_infer')
              AND identity_status = 'verified'
            """,
            (past, *fund_codes),
        )
        expired = int(cursor.rowcount or 0)
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
