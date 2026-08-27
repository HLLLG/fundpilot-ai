#!/usr/bin/env python3
"""对所有用户持仓强制重跑关联板块，并打印重算前后对照。

主动基金走季报穿透（绕过已有 holdings_infer/precompute_holdings 短路）；
被动指数/联接只复核合同跟踪指数。

用法（apps/api 或生产 api 容器 /app）：
    python scripts/rerun_holdings_primary_sectors.py inspect
    python scripts/rerun_holdings_primary_sectors.py run
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def _iter_user_holdings():
    from app.database import list_distinct_portfolio_user_ids
    from app.request_context import reset_request_user_id, set_request_user_id
    from app.services.portfolio_holdings_service import load_persisted_holdings

    for user_id in list_distinct_portfolio_user_ids():
        token = set_request_user_id(user_id)
        try:
            holdings, source, snapshot_date, _ = load_persisted_holdings(
                fetch_benchmark=False
            )
            yield user_id, holdings, source, snapshot_date
        finally:
            reset_request_user_id(token)


def inspect() -> int:
    from app.database import get_fund_primary_sectors_by_codes
    from app.request_context import reset_request_user_id, set_request_user_id

    for user_id, holdings, source, snapshot_date in _iter_user_holdings():
        token = set_request_user_id(user_id)
        try:
            codes = [
                holding.fund_code
                for holding in holdings
                if holding.fund_code and holding.fund_code != "000000"
            ]
            rows = get_fund_primary_sectors_by_codes(codes) if codes else {}
        finally:
            reset_request_user_id(token)
        print(
            f"=== user {user_id} source={source} snapshot={snapshot_date} "
            f"n={len(holdings)} ==="
        )
        for holding in holdings:
            row = rows.get(holding.fund_code) or {}
            print(
                "\t".join(
                    [
                        holding.fund_code,
                        holding.fund_name,
                        f"sector={holding.sector_name}",
                        f"index={holding.intraday_index_name}",
                        f"source={row.get('source')}",
                    ]
                )
            )
    return 0


def _fields(record) -> dict[str, str]:
    from app.services.fund_profile import infer_intraday_index_from_sector

    fields: dict[str, str] = {"sector_name": record.sector_name}
    index_name = record.intraday_index_name or infer_intraday_index_from_sector(
        record.sector_name
    )
    if index_name:
        fields["intraday_index_name"] = index_name
    return fields


def run() -> int:
    from app.database import save_fund_primary_sector
    from app.request_context import reset_request_user_id, set_request_user_id
    from app.services.fund_primary_sector_global import promote_record_to_global
    from app.services.fund_primary_sector_service import (
        _is_passive_index_fund_name,
        _resolve_from_holdings_infer,
        resolve_primary_sector,
    )
    from app.services.portfolio_persistence import persist_holdings_after_sector_refresh

    changed_total = 0
    for user_id, holdings, _source, _snapshot_date in _iter_user_holdings():
        token = set_request_user_id(user_id)
        try:
            updated = []
            changed = 0
            for holding in holdings:
                code = (holding.fund_code or "").strip()
                if not code or code == "000000":
                    updated.append(holding)
                    continue
                before = (holding.sector_name, holding.intraday_index_name)
                if _is_passive_index_fund_name(holding.fund_name):
                    record = resolve_primary_sector(
                        code,
                        fund_name=holding.fund_name,
                        fetch_benchmark=True,
                        fetch_holdings_infer=False,
                    )
                    path = "benchmark"
                else:
                    record = _resolve_from_holdings_infer(
                        code,
                        persist=False,
                        fund_name=holding.fund_name,
                    )
                    path = "holdings_infer"
                    if record is None:
                        record = resolve_primary_sector(
                            code,
                            fund_name=holding.fund_name,
                            fetch_benchmark=True,
                            fetch_holdings_infer=False,
                        )
                        path = "fallback"
                if record is None:
                    print(
                        json.dumps(
                            {
                                "user_id": user_id,
                                "fund_code": code,
                                "fund_name": holding.fund_name,
                                "path": path,
                                "ok": False,
                            },
                            ensure_ascii=False,
                        )
                    )
                    updated.append(holding)
                    continue
                if path == "holdings_infer":
                    save_fund_primary_sector(
                        fund_code=code,
                        sector_name=record.sector_name,
                        intraday_index_name=record.intraday_index_name,
                        source="holdings_infer",
                        confidence=record.confidence,
                        detail=record.detail,
                    )
                    promote_record_to_global(record)
                fields = _fields(record)
                next_holding = holding.model_copy(update=fields)
                after = (next_holding.sector_name, next_holding.intraday_index_name)
                did = after != before
                if did:
                    changed += 1
                    changed_total += 1
                print(
                    json.dumps(
                        {
                            "user_id": user_id,
                            "fund_code": code,
                            "fund_name": holding.fund_name,
                            "before_sector": before[0],
                            "after_sector": after[0],
                            "before_index": before[1],
                            "after_index": after[1],
                            "path": path,
                            "source": record.source,
                            "changed": did,
                            "ok": True,
                        },
                        ensure_ascii=False,
                    )
                )
                updated.append(next_holding)
            if changed:
                persist_holdings_after_sector_refresh(
                    updated, with_official_nav=False
                )
        finally:
            reset_request_user_id(token)
    print(json.dumps({"changed_total": changed_total}, ensure_ascii=False))
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    if command == "inspect":
        return inspect()
    if command == "run":
        return run()
    print("usage: rerun_holdings_primary_sectors.py [inspect|run]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
