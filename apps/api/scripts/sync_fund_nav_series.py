#!/usr/bin/env python3
"""全市场滚动 3 年净值：日更、历史回填、自算 1/3 年回撤与夏普。

用法（在 apps/api 下，或生产容器 WORKDIR=/app）：
    python scripts/sync_fund_nav_series.py --daily
    python scripts/sync_fund_nav_series.py --backfill
    python scripts/sync_fund_nav_series.py --backfill --limit 200
    python scripts/sync_fund_nav_series.py --purge-expired
    python scripts/sync_fund_nav_series.py --recompute-risk
    python scripts/sync_fund_nav_series.py --all

日更走东财开放式净值全表（一次请求）。3 年历史只能逐只回填，可断点续跑。
过期点（早于今天往前 3 个自然年）分批删除，避免整表 DELETE 把 MySQL 读超时打穿。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.database import get_fund_nav_series_meta  # noqa: E402
from app.services.fund_nav_series import (  # noqa: E402
    backfill_fund_nav_series,
    purge_expired_fund_nav_series,
    run_daily_nav_series_and_risk,
)
from app.services.fund_risk_metrics import (  # noqa: E402
    refresh_fund_risk_metrics_from_nav_series,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="同步全市场 3 年净值并自算回撤/夏普")
    parser.add_argument(
        "--daily",
        action="store_true",
        help="拉全市场最新 1～2 日净值并按表重算 1/3 年回撤与夏普",
    )
    parser.add_argument("--backfill", action="store_true", help="逐只回填近 800 个交易日")
    parser.add_argument("--limit", type=int, default=None, help="本轮最多回填几只")
    parser.add_argument("--force", action="store_true", help="无视已回填记录，重新拉历史")
    parser.add_argument("--purge-expired", action="store_true", help="只分批删除 3 年以前的净值点")
    parser.add_argument("--recompute-risk", action="store_true", help="只按已入库净值重算风险")
    parser.add_argument("--all", action="store_true", help="日更 + 回填剩余代码 + 重算风险")
    parser.add_argument("--json", dest="json_out", default=None, help="把摘要写到该路径")
    args = parser.parse_args()
    if not any(
        (args.daily, args.backfill, args.recompute_risk, args.all, args.purge_expired)
    ):
        args.all = True

    summary: dict = {"ok": False}
    if args.all or args.daily:
        print(f"[{_now()}] 开始全市场净值日更", flush=True)
        daily_summary = run_daily_nav_series_and_risk()
        summary["daily"] = daily_summary.get("daily")
        summary["risk_written"] = daily_summary.get("risk_written")
        daily = summary.get("daily") or {}
        print(
            f"  写入 {daily.get('written')}  删除过期 {daily.get('purged')}  "
            f"最新日 {daily.get('latest_date')}  风险 {daily_summary.get('risk_written')}",
            flush=True,
        )
        if daily.get("error"):
            print(f"  !! {daily['error']}", file=sys.stderr)

    if args.purge_expired and not (args.all or args.daily or args.backfill):
        print(f"[{_now()}] 开始分批删除过期净值", flush=True)
        purged = purge_expired_fund_nav_series()
        summary["purged"] = purged
        print(f"  删除过期 {purged}", flush=True)

    if args.all or args.backfill:
        print(f"[{_now()}] 开始历史净值回填", flush=True)
        backfill = backfill_fund_nav_series(limit=args.limit, force=args.force)
        summary["backfill"] = backfill
        print(
            f"  本轮拉取 {backfill.get('fetched')}  写入 {backfill.get('written')}  "
            f"剩余 {backfill.get('remaining')}",
            flush=True,
        )
        if args.all or args.recompute_risk:
            written = refresh_fund_risk_metrics_from_nav_series()
            summary["risk_written"] = written
            print(f"  回填后风险重算 {written}", flush=True)

    elif args.recompute_risk:
        print(f"[{_now()}] 按净值表重算回撤/夏普", flush=True)
        written = refresh_fund_risk_metrics_from_nav_series()
        summary["risk_written"] = written
        print(f"  写入 {written}", flush=True)

    try:
        meta = get_fund_nav_series_meta() or {}
    except Exception as exc:  # noqa: BLE001 - 摘要查询超时不应盖过已完成的写入
        print(f"  !! 表摘要查询失败：{exc}", file=sys.stderr)
        meta = {}
    summary["series"] = meta
    summary["ok"] = bool(
        int(meta.get("row_count") or 0) > 0
        or summary.get("risk_written")
        or summary.get("purged")
        or (summary.get("daily") or {}).get("written")
        or (summary.get("backfill") or {}).get("fetched")
    )
    print()
    print(f"  表内点数          {meta.get('row_count')}")
    print(f"  覆盖基金          {meta.get('fund_count')}")
    print(f"  日期区间          {meta.get('first_nav_date')} ~ {meta.get('last_nav_date')}")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  摘要已写入 {out_path}")

    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
