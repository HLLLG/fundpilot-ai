#!/usr/bin/env python3
"""全市场基金 → 关联板块 离线预计算。

用法（在 apps/api 下）：
    python scripts/precompute_fund_primary_sectors.py --limit 200 --mode benchmark
    python scripts/precompute_fund_primary_sectors.py --codes 021533,519674 --force
    python scripts/precompute_fund_primary_sectors.py --mode auto --limit 50

结果写入 SQLite/MySQL 表 ``fund_primary_sectors_global``；状态文件 ``data/fund_primary_sector_precompute_status.json``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.database import count_fund_primary_sectors_global  # noqa: E402
from app.services.fund_primary_sector_precompute import (  # noqa: E402
    load_precompute_status,
    run_precompute_batch,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="预计算全市场基金主关联板块")
    parser.add_argument("--limit", type=int, default=None, help="本批最多处理多少只基金")
    parser.add_argument(
        "--mode",
        choices=("benchmark", "holdings", "auto"),
        default="benchmark",
        help="benchmark=仅业绩基准；holdings=仅重仓穿透；auto=先基准后穿透",
    )
    parser.add_argument("--force", action="store_true", help="忽略 TTL，强制重算")
    parser.add_argument(
        "--codes",
        type=str,
        default="",
        help="逗号分隔基金代码；指定时仅处理这些代码",
    )
    parser.add_argument("--sleep", type=float, default=0.05, help="每只基金间隔秒数")
    args = parser.parse_args()

    codes = [part.strip() for part in args.codes.split(",") if part.strip()] or None
    before = count_fund_primary_sectors_global()
    result = run_precompute_batch(
        limit=args.limit,
        mode=args.mode,
        force=args.force,
        fund_codes=codes,
        sleep_seconds=max(0.0, args.sleep),
    )
    after = count_fund_primary_sectors_global()
    payload = {
        "before_count": before,
        "after_count": after,
        **result.to_dict(),
        "last_status": load_precompute_status(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.error == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
