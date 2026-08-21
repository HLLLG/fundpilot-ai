#!/usr/bin/env python3
"""全市场基金关联板块强制重算（约两万只，不只是用户持仓）。

官方 CLI 不能直接承担这次任务：
- ``--until-covered --force`` 在 force 时永远取宇宙前 800 只，不会翻页；
- ``--mode holdings --force`` 会跳过已 verified 的身份，医疗不会升成 CXO。

本脚本：
    python scripts/rerun_all_primary_sectors.py inspect
    python scripts/rerun_all_primary_sectors.py run
    python scripts/rerun_all_primary_sectors.py run --holdings-limit 64

主动基金走季报穿透并允许覆盖同优先级 ``precompute_holdings``；
被动指数/联接只走档案重分类，禁止持仓行业改写跟踪指数。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

_STATUS_NAME = "rerun_all_primary_sectors_status.json"
_HOLDINGS_SOURCES = frozenset({"holdings_infer", "precompute_holdings"})
_EQUITY_CATEGORY_TOKENS = ("股票", "指数", "混合")
_SKIP_UNMAPPED_REASONS = frozenset(
    {
        "no_sector_identity_expected",
        "money_market_or_bond_unmapped",
    }
)


def _status_path() -> Path:
    from app.config import get_settings

    return get_settings().db_path.parent / _STATUS_NAME


def _load_progress() -> dict:
    path = _status_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_progress(payload: dict) -> None:
    path = _status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _db_rows(sql: str, params: tuple = ()) -> list[dict]:
    from app.database import _connect

    with _connect() as connection:
        cursor = connection.execute(sql, params)
        return [
            row if isinstance(row, dict) else dict(row)
            for row in cursor.fetchall()
        ]


def _group_counts(sql: str) -> list[dict]:
    return _db_rows(sql)


def inspect() -> int:
    from app.database import (
        count_fresh_verified_fund_sector_current,
        count_fund_primary_sectors_global,
    )
    from app.services.fund_code_resolver import _fund_name_table
    from app.services.fund_primary_sector_precompute import (
        load_precompute_status,
        resolution_coverage,
    )

    universe = len(_fund_name_table())
    coverage = resolution_coverage()
    current_groups = _group_counts(
        """
        SELECT source, identity_status, COUNT(*) AS cnt
        FROM fund_sector_current
        WHERE is_primary = 1
        GROUP BY source, identity_status
        ORDER BY cnt DESC
        """
    )
    resolution_groups = _group_counts(
        """
        SELECT resolution_status, stage, COUNT(*) AS cnt
        FROM fund_sector_resolution_status
        GROUP BY resolution_status, stage
        ORDER BY cnt DESC
        """
    )
    snapshot_row = _db_rows(
        "SELECT COUNT(DISTINCT fund_code) AS cnt FROM fund_holdings_snapshots"
    )
    reason_groups = _group_counts(
        """
        SELECT resolution_status, reason_code, COUNT(*) AS cnt
        FROM fund_sector_resolution_status
        GROUP BY resolution_status, reason_code
        ORDER BY cnt DESC
        """
    )
    from app.database import (
        get_fund_sector_current_primary_by_codes,
        list_fund_sector_resolution_statuses,
    )
    from app.services.fund_code_resolver import _fund_name_table as _names

    name_by_code = {
        str(code).strip().zfill(6): str(name or "").strip()
        for code, name in _names()
        if str(code or "").strip()
    }
    holdings_targets = _holdings_targets(
        statuses=list_fund_sector_resolution_statuses(),
        current_rows=get_fund_sector_current_primary_by_codes(set(name_by_code)),
        name_by_code=name_by_code,
    )
    _emit(
        {
            "universe_size": universe,
            "global_count": count_fund_primary_sectors_global(),
            "verified_current_count": count_fresh_verified_fund_sector_current(),
            "holdings_snapshot_funds": int((snapshot_row[0] or {}).get("cnt") or 0)
            if snapshot_row
            else 0,
            "holdings_rerun_targets": len(holdings_targets),
            "reason_groups": reason_groups[:40],
            **coverage,
            "current_primary_groups": current_groups,
            "resolution_groups": resolution_groups,
            "last_precompute_status": load_precompute_status(),
            "rerun_progress": _load_progress(),
        }
    )
    return 0


def _decoded_detail(row: dict | None) -> dict:
    if not row:
        return {}
    raw = row.get("detail")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _holdings_targets(
    *,
    statuses: dict[str, dict],
    current_rows: dict[str, dict],
    name_by_code: dict[str, str],
) -> list[str]:
    from app.services.fund_primary_sector_service import _is_passive_index_fund_name

    ranked: list[tuple[tuple[object, ...], str]] = []
    theme_old = {"医疗", "通信技术", "计算机", "软件", "互联网"}
    for code, name in name_by_code.items():
        if _is_passive_index_fund_name(name):
            continue
        current = current_rows.get(code) or {}
        status_row = statuses.get(code) or {}
        status = str(status_row.get("resolution_status") or "")
        stage = str(status_row.get("stage") or "")
        reason = str(status_row.get("reason_code") or "")
        detail = _decoded_detail(status_row)
        category = str(detail.get("fund_category") or "")
        source = str(current.get("source") or "")
        sector = str(current.get("sector_name") or "")
        selected = False
        if source in _HOLDINGS_SOURCES:
            selected = True
        elif status in {"queued", "pending"}:
            selected = True
        elif stage == "holdings_resolution" and status in {
            "research_only",
            "unavailable",
        }:
            selected = True
        elif status == "unmapped" and reason in _SKIP_UNMAPPED_REASONS:
            selected = False
        elif detail.get("semantic_recall_sector") or any(
            token in category for token in _EQUITY_CATEGORY_TOKENS
        ):
            selected = True
        if not selected:
            continue
        priority = (
            0
            if source in _HOLDINGS_SOURCES
            and str(current.get("identity_status") or "") == "verified"
            else 1
            if sector in theme_old
            else 2
            if status in {"queued", "pending"}
            else 3,
            code,
        )
        ranked.append((priority, code))
    ranked.sort(key=lambda item: item[0])
    return [code for _priority, code in ranked]


def _run_profile_pass(
    *,
    refetch: bool,
    batch_size: int,
    sleep_seconds: float,
    network_backfill: bool = True,
) -> dict:
    from app.services.fund_code_resolver import _fund_name_table
    from app.services.fund_primary_sector_precompute import (
        resolution_coverage,
        run_precompute_batch,
    )

    summary = {
        "reclassified": None,
        "coverage_after_reclassify": None,
        "profile_batches": 0,
        "profile_processed": 0,
    }
    coverage = resolution_coverage()
    if not network_backfill or (not refetch and coverage.get("initial_backfill_complete")):
        return summary

    codes = [
        str(code).strip().zfill(6)
        for code, _name in _fund_name_table()
        if str(code or "").strip()
    ]
    if refetch:
        for start in range(0, len(codes), batch_size):
            chunk = codes[start : start + batch_size]
            result = run_precompute_batch(
                limit=len(chunk),
                mode="benchmark",
                force=True,
                fund_codes=chunk,
                sleep_seconds=sleep_seconds,
            )
            summary["profile_batches"] += 1
            summary["profile_processed"] += result.processed
            _emit(
                {
                    "phase": "profile_refetch",
                    "batch": summary["profile_batches"],
                    "offset": start,
                    **result.to_dict(),
                    **resolution_coverage(),
                }
            )
            time.sleep(1.0)
        return summary

    while True:
        result = run_precompute_batch(
            mode="benchmark",
            force=False,
            sleep_seconds=sleep_seconds,
        )
        summary["profile_batches"] += 1
        summary["profile_processed"] += result.processed
        coverage = resolution_coverage()
        _emit(
            {
                "phase": "profile_backfill",
                "batch": summary["profile_batches"],
                **result.to_dict(),
                **coverage,
            }
        )
        if result.processed <= 0 or coverage.get("initial_backfill_complete"):
            break
        time.sleep(1.0)
    return summary


def _run_holdings_pass(
    *,
    batch_size: int,
    workers: int,
    sleep_seconds: float,
    limit: int | None,
    resume: bool,
) -> dict:
    from app.database import (
        get_fund_primary_sectors_global_by_codes,
        get_fund_sector_current_primary_by_codes,
        list_fund_sector_resolution_statuses,
        save_fund_sector_resolution_statuses,
    )
    from app.services.fund_code_resolver import _fund_name_table
    from app.services.fund_primary_sector_precompute import (
        _HOLDINGS_RESOLUTION_STAGE,
        _evaluate_holdings_resolution,
        _fetch_holdings_evidence_batch,
        _promote_and_remember,
        _resolution_status_row,
        resolution_coverage,
    )

    name_by_code = {
        str(code).strip().zfill(6): str(name or "").strip()
        for code, name in _fund_name_table()
        if str(code or "").strip()
    }
    statuses = list_fund_sector_resolution_statuses()
    current_rows = get_fund_sector_current_primary_by_codes(set(name_by_code))
    targets = _holdings_targets(
        statuses=statuses,
        current_rows=current_rows,
        name_by_code=name_by_code,
    )
    progress = _load_progress() if resume else {}
    cursor = int(progress.get("holdings_cursor") or 0) if resume else 0
    if cursor > len(targets):
        cursor = 0
    remaining = targets[cursor:]
    if limit is not None:
        remaining = remaining[: max(0, int(limit))]

    totals = Counter(
        {
            "targets": len(targets),
            "remaining": len(remaining),
            "ok": 0,
            "miss": 0,
            "error": 0,
            "changed": 0,
            "processed": 0,
        }
    )
    _emit(
        {
            "phase": "holdings_start",
            "targets": len(targets),
            "resume_cursor": cursor,
            "this_run": len(remaining),
        }
    )

    started = datetime.now(timezone.utc)
    for start in range(0, len(remaining), batch_size):
        chunk = remaining[start : start + batch_size]
        global_rows = get_fund_primary_sectors_global_by_codes(set(chunk))
        before_rows = get_fund_sector_current_primary_by_codes(set(chunk))
        evidence = _fetch_holdings_evidence_batch(chunk, workers=workers)
        checkpoints: list[dict] = []
        checked_at = datetime.now(timezone.utc)
        for code in chunk:
            totals["processed"] += 1
            try:
                evaluation = _evaluate_holdings_resolution(
                    code,
                    evidence.get(
                        code,
                        {
                            "status": "unavailable",
                            "reason_codes": ["holdings_evidence_result_missing"],
                            "stocks": [],
                        },
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - one fund must not stop the market
                totals["error"] += 1
                _emit(
                    {
                        "phase": "holdings_error",
                        "fund_code": code,
                        "error": type(exc).__name__,
                    }
                )
                continue
            before_sector = str((before_rows.get(code) or {}).get("sector_name") or "")
            if evaluation.record is not None:
                _promote_and_remember(
                    evaluation.record,
                    source="precompute_holdings",
                    global_rows_by_code=global_rows,
                )
                totals["ok"] += 1
                after_sector = evaluation.record.sector_name
                changed = after_sector != before_sector
                if changed:
                    totals["changed"] += 1
                _emit(
                    {
                        "phase": "holdings_item",
                        "fund_code": code,
                        "fund_name": name_by_code.get(code),
                        "before_sector": before_sector or None,
                        "after_sector": after_sector,
                        "status": evaluation.resolution_status,
                        "changed": changed,
                    }
                )
            else:
                totals["miss"] += 1
                _emit(
                    {
                        "phase": "holdings_item",
                        "fund_code": code,
                        "fund_name": name_by_code.get(code),
                        "before_sector": before_sector or None,
                        "after_sector": None,
                        "status": evaluation.resolution_status,
                        "reason_code": evaluation.reason_code,
                        "changed": False,
                    }
                )
            checkpoints.append(
                _resolution_status_row(
                    fund_code=code,
                    fund_name=name_by_code.get(code) or None,
                    status=evaluation.resolution_status,
                    reason_code=evaluation.reason_code,
                    detail=evaluation.detail,
                    previous=statuses.get(code),
                    checked_at=checked_at,
                    stage=_HOLDINGS_RESOLUTION_STAGE,
                )
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        if checkpoints:
            save_fund_sector_resolution_statuses(checkpoints)
        next_cursor = cursor + start + len(chunk)
        _save_progress(
            {
                "started_at": progress.get("started_at") or started.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "holdings_cursor": next_cursor,
                "holdings_targets": len(targets),
                **dict(totals),
            }
        )
        _emit(
            {
                "phase": "holdings_batch",
                "cursor": next_cursor,
                "batch_size": len(chunk),
                **dict(totals),
                **resolution_coverage(),
            }
        )
    return dict(totals)


def run(args: argparse.Namespace) -> int:
    from app.services.fund_primary_sector_precompute import resolution_coverage

    started = datetime.now(timezone.utc)
    _save_progress(
        {
            **(_load_progress() if args.resume else {}),
            "started_at": started.isoformat(),
            "phase": "profile",
        }
    )
    profile_summary = _run_profile_pass(
        refetch=args.refetch_profiles,
        batch_size=max(1, args.profile_batch_size),
        sleep_seconds=max(0.0, args.sleep),
        network_backfill=args.refetch_profiles,
    )
    holdings_summary = _run_holdings_pass(
        batch_size=max(1, args.holdings_batch_size),
        workers=max(1, args.workers),
        sleep_seconds=max(0.0, args.sleep),
        limit=args.holdings_limit,
        resume=args.resume,
    )
    finished = {
        "ok": True,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile_summary,
        "holdings": holdings_summary,
        **resolution_coverage(),
    }
    _save_progress(finished)
    print(json.dumps(finished, ensure_ascii=False, indent=2), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场基金关联板块强制重算")
    parser.add_argument("command", choices=("inspect", "run"), default="inspect", nargs="?")
    parser.add_argument(
        "--refetch-profiles",
        action="store_true",
        help="对两万只全部重新拉档案；默认跳过，只强制主动基金持仓穿透",
    )
    parser.add_argument("--resume", action="store_true", help="从上次 holdings 游标继续")
    parser.add_argument("--profile-batch-size", type=int, default=800)
    parser.add_argument("--holdings-batch-size", type=int, default=32)
    parser.add_argument("--holdings-limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()
    if args.command == "inspect":
        return inspect()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
