#!/usr/bin/env python3
"""全市场基金 → 关联板块 离线预计算。

用法（在 apps/api 下）：
    python scripts/precompute_fund_primary_sectors.py --limit 200 --mode benchmark
    python scripts/precompute_fund_primary_sectors.py --mode benchmark --until-covered
    python scripts/precompute_fund_primary_sectors.py --codes 021533,519674 --force
    python scripts/precompute_fund_primary_sectors.py --mode auto --limit 50

结果写入 PIT 身份表、当前投影与逐基金解析状态表；状态文件为
``data/fund_primary_sector_precompute_status.json``。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.database import (  # noqa: E402
    count_fresh_verified_fund_sector_current,
    count_fund_primary_sectors_global,
    list_fund_sector_resolution_statuses,
)
from app.config import get_settings  # noqa: E402
from app.services.fund_primary_sector_precompute import (  # noqa: E402
    load_precompute_status,
    reclassify_stored_profile_resolutions,
    resolution_coverage,
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
    parser.add_argument(
        "--until-covered",
        action="store_true",
        help="连续运行 benchmark 批次，直到每个基金代码都有明确解析状态",
    )
    parser.add_argument(
        "--batch-pause",
        type=float,
        default=1.0,
        help="连续全量回填时的批次间隔秒数",
    )
    parser.add_argument(
        "--retry-status",
        choices=(
            "unavailable",
            "queued",
            "research_only",
            "pending",
            "unmapped",
            "verified",
        ),
        default=None,
        help="把运行开始时处于该状态的代码全部强制重试一轮",
    )
    parser.add_argument(
        "--retry-reason",
        type=str,
        default=None,
        help="把运行开始时具有该 reason_code 的代码全部强制重试一轮",
    )
    parser.add_argument(
        "--reclassify-reason",
        type=str,
        default=None,
        help="逗号分隔 reason_code；只用已落盘资料重跑目录规则，不访问上游",
    )
    args = parser.parse_args()

    codes = [part.strip() for part in args.codes.split(",") if part.strip()] or None
    reclassify_reasons = {
        part.strip()
        for part in str(args.reclassify_reason or "").split(",")
        if part.strip()
    }
    if args.until_covered and (args.mode != "benchmark" or codes):
        parser.error("--until-covered 仅支持未指定 --codes 的 benchmark 模式")
    retry_selector_used = bool(args.retry_status or args.retry_reason)
    if args.retry_status and args.retry_reason:
        parser.error("--retry-status 与 --retry-reason 不能同时使用")
    if reclassify_reasons and (
        args.mode != "benchmark"
        or codes
        or args.until_covered
        or retry_selector_used
        or args.force
    ):
        parser.error("--reclassify-reason 只能独立用于 benchmark 模式")
    if retry_selector_used and (args.mode != "benchmark" or codes or args.until_covered):
        parser.error("重试筛选仅支持未指定 --codes/--until-covered 的 benchmark 模式")
    retry_codes = None
    if retry_selector_used:
        retry_codes = sorted(
            code
            for code, row in list_fund_sector_resolution_statuses().items()
            if (
                str(row.get("resolution_status") or "") == args.retry_status
                if args.retry_status
                else str(row.get("reason_code") or "") == args.retry_reason
            )
        )
    retry_offset = 0
    retry_batch_size = max(
        1,
        int(
            args.limit
            if args.limit is not None
            else get_settings().fund_primary_sector_precompute_batch_size
        ),
    )
    before = count_fund_primary_sectors_global()
    verified_before = count_fresh_verified_fund_sector_current()
    if reclassify_reasons:
        result = reclassify_stored_profile_resolutions(
            reason_codes=reclassify_reasons,
            limit=args.limit,
        )
        print(
            json.dumps(
                {
                    "before_count": before,
                    "after_count": count_fund_primary_sectors_global(),
                    "verified_before_count": verified_before,
                    "verified_after_count": count_fresh_verified_fund_sector_current(),
                    "reclassify_reasons": sorted(reclassify_reasons),
                    **result.to_dict(),
                    **resolution_coverage(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.error == 0 else 1
    total = {
        "ok": 0,
        "skipped": 0,
        "miss": 0,
        "error": 0,
        "processed": 0,
        "queued": 0,
        "research_only": 0,
        "pending": 0,
        "unmapped": 0,
        "unavailable": 0,
        "errors": [],
    }
    batch_number = 0
    result = None
    while True:
        batch_number += 1
        current_codes = codes
        current_force = args.force
        if retry_codes is not None:
            current_codes = retry_codes[retry_offset : retry_offset + retry_batch_size]
            if not current_codes:
                batch_number -= 1
                break
            current_force = True
        result = run_precompute_batch(
            limit=args.limit,
            mode=args.mode,
            force=current_force,
            fund_codes=current_codes,
            sleep_seconds=max(0.0, args.sleep),
        )
        payload = result.to_dict()
        for key in (
            "ok",
            "skipped",
            "miss",
            "error",
            "processed",
            "queued",
            "research_only",
            "pending",
            "unmapped",
            "unavailable",
        ):
            total[key] += int(payload.get(key) or 0)
        total["errors"] = [*total["errors"], *payload.get("errors", [])][:20]
        coverage = resolution_coverage()
        if args.until_covered or retry_codes is not None:
            print(
                json.dumps(
                    {
                        "batch": batch_number,
                        "retry_status": args.retry_status,
                        "retry_reason": args.retry_reason,
                        **payload,
                        **coverage,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if not args.until_covered:
            if retry_codes is None:
                break
            retry_offset += len(current_codes or [])
            if retry_offset >= len(retry_codes):
                break
            time.sleep(max(0.0, args.batch_pause))
            continue
        if bool(coverage.get("initial_backfill_complete")) or result.processed <= 0:
            break
        time.sleep(max(0.0, args.batch_pause))
    after = count_fund_primary_sectors_global()
    verified_after = count_fresh_verified_fund_sector_current()
    payload = {
        "before_count": before,
        "after_count": after,
        "verified_before_count": verified_before,
        "verified_after_count": verified_after,
        "batches": batch_number,
        "retry_status": args.retry_status,
        "retry_reason": args.retry_reason,
        "retry_candidate_count": len(retry_codes or []),
        **total,
        **resolution_coverage(),
        "last_status": load_precompute_status(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result is None or result.error == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
