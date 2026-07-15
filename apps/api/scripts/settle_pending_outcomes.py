#!/usr/bin/env python3
"""结算正式 T+N 结果及前瞻注册的荐基候选 T+20 标签。

推荐由服务器 cron 在收盘净值基本发布后执行，例如：
    python scripts/settle_pending_outcomes.py
    python scripts/settle_pending_outcomes.py --user-id 1
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.candidate_selection_outcomes import (  # noqa: E402
    settle_candidate_selection_outcomes,
)
from app.services.decision_repository import (  # noqa: E402
    reconcile_decision_quality_artifact_receipts,
)
from app.services.outcome_settlement import settle_pending_outcomes  # noqa: E402


def _failed_closed_result(exc: Exception) -> dict[str, Any]:
    return {
        "status": "failed_closed",
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


def _reconcile_receipts(
    *,
    user_ids: Sequence[int] | None,
    limit: int,
) -> dict[str, Any]:
    if not user_ids:
        return reconcile_decision_quality_artifact_receipts(limit=limit)
    results = [
        reconcile_decision_quality_artifact_receipts(
            user_id=user_id,
            limit=limit,
        )
        for user_id in sorted(set(user_ids))
    ]
    failures = [
        failure
        for result in results
        for failure in result.get("failures", [])
        if isinstance(failure, dict)
    ]
    return {
        "status": "completed" if not failures else "completed_with_failures",
        "scanned_count": sum(int(row.get("scanned_count") or 0) for row in results),
        "finalized_count": sum(
            int(row.get("finalized_count") or 0) for row in results
        ),
        "failed_count": len(failures),
        "finalized_artifact_ids": sorted(
            {
                str(artifact_id)
                for row in results
                for artifact_id in row.get("finalized_artifact_ids", [])
            }
        ),
        "failures": failures,
        "user_count": len(results),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自动结算正式决策的 pending T+N 结果")
    parser.add_argument(
        "--user-id",
        type=int,
        action="append",
        dest="user_ids",
        help="仅处理指定用户；可重复。省略时处理全部有 pending 记录的用户",
    )
    parser.add_argument("--as-of-date", default=None, help="结算截止日（YYYY-MM-DD）")
    parser.add_argument("--max-reports", type=int, default=500)
    parser.add_argument("--max-candidate-cases", type=int, default=20)
    parser.add_argument("--max-receipts", type=int, default=500)
    args = parser.parse_args(argv)

    failed = False
    try:
        receipt_result = _reconcile_receipts(
            user_ids=args.user_ids,
            limit=max(1, int(args.max_receipts)),
        )
        if receipt_result.get("failed_count"):
            failed = True
    except Exception as exc:  # noqa: BLE001 - other jobs must still run
        receipt_result = _failed_closed_result(exc)
        failed = True
    try:
        result = settle_pending_outcomes(
            user_ids=args.user_ids,
            as_of_date=args.as_of_date,
            max_reports=args.max_reports,
        )
    # Isolate both jobs even when a service leaks an unexpected exception.
    except Exception as exc:  # noqa: BLE001
        result = _failed_closed_result(exc)
        failed = True

    try:
        candidate_result = settle_candidate_selection_outcomes(
            user_ids=args.user_ids,
            as_of_date=args.as_of_date,
            max_cases=args.max_candidate_cases,
        )
    except Exception as exc:  # noqa: BLE001
        candidate_result = _failed_closed_result(exc)
        failed = True

    result["candidate_selection"] = candidate_result
    result["decision_quality_artifact_receipts"] = receipt_result
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if (
        failed
        or result.get("failed_user_ids")
        or candidate_result.get("failed_user_ids")
    ):
        return 2
    # Unrecoverable legacy orphans remain visible in the summary, but must not
    # make every future daily run red or block settlement for healthy reports.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
