#!/usr/bin/env python3
"""结算日报/荐基正式 V2 的 pending T+N 观察结果。

推荐由服务器 cron 在收盘净值基本发布后执行，例如：
    python scripts/settle_pending_outcomes.py
    python scripts/settle_pending_outcomes.py --user-id 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.outcome_settlement import (  # noqa: E402
    OutcomeSettlementError,
    settle_pending_outcomes,
)


def main() -> int:
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
    args = parser.parse_args()

    try:
        result = settle_pending_outcomes(
            user_ids=args.user_ids,
            as_of_date=args.as_of_date,
            max_reports=args.max_reports,
        )
    except OutcomeSettlementError as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Unrecoverable legacy orphans remain visible in the summary, but must not
    # make every future daily run red or block settlement for healthy reports.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
