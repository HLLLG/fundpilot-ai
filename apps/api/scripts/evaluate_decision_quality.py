#!/usr/bin/env python3
"""Evaluate frozen decision evidence and append immutable quality snapshots.

This operations command is intentionally point-in-time: callers must supply an
aware ``--evaluation-as-of`` cutoff.  It never fetches providers or promotes a
challenger; insufficient samples are a successful, persisted observation.
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

from app.services.decision_quality_snapshot import (  # noqa: E402
    DEFAULT_WINDOW_DAYS,
    DecisionQualitySnapshotError,
    evaluate_and_persist_decision_quality_snapshots,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从主证据库运行点时决策质量评估，并默认持久化不可变快照",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--user-id",
        type=int,
        action="append",
        dest="user_ids",
        help="仅评估指定用户；可重复",
    )
    scope.add_argument(
        "--all-users",
        action="store_true",
        help="评估所有已有正式决策证据或冻结输入制品的用户",
    )
    parser.add_argument(
        "--evaluation-as-of",
        required=True,
        help="评估截止时点，必须为带时区的 ISO 8601 时间",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"回看窗口天数（默认 {DEFAULT_WINDOW_DAYS}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只验证与评估，不写入快照；默认写入",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "json"),
        default="summary",
        dest="output_format",
        help="输出格式（默认 summary）",
    )
    return parser


def _print_summary(result: dict[str, Any]) -> None:
    print(
        "decision-quality evaluation completed: "
        f"as_of={result['evaluation_as_of']} "
        f"window_days={result['window_days']} "
        f"users={result['user_count']} "
        f"persisted={str(result['persisted']).lower()} "
        "automatic_promotion_allowed=false"
    )
    for row in result.get("snapshots", []):
        if not isinstance(row, dict):
            continue
        coverage = row.get("formal_label_coverage_percent")
        coverage_text = "unavailable" if coverage is None else f"{coverage:.2f}%"
        print(
            f"user_id={row.get('user_id')} "
            f"readiness={row.get('readiness_status')} "
            f"mature_days={row.get('mature_decision_day_count')} "
            f"formal_label_coverage={coverage_text} "
            f"evaluation_status={row.get('status')} "
            f"snapshot_id={row.get('snapshot_id')}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = evaluate_and_persist_decision_quality_snapshots(
            evaluation_as_of=args.evaluation_as_of,
            user_ids=None if args.all_users else args.user_ids,
            window_days=args.window_days,
            persist=not args.dry_run,
        )
    except (DecisionQualitySnapshotError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "automatic_promotion_allowed": False,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    if args.output_format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
