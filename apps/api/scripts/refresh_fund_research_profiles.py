#!/usr/bin/env python3
"""整包刷新开放式基金规模档案，以及东财经理累计从业天数。

用法（在 apps/api 下，或生产容器 WORKDIR=/app）：
    python scripts/refresh_fund_research_profiles.py
    python scripts/refresh_fund_research_profiles.py --if-stale
    python scripts/refresh_fund_research_profiles.py --json var/fund_research_profile_refresh.json

为什么需要它：`fund_research_profile` 不能在用户跑一次发现基金时才拉新浪四张全表。
规模优先季报净资产，没有报告期净值时用当日净值 × 上季份额兜底，
由收盘后定时任务/后台 worker 写入，荐基请求路径只 JOIN。

退出码：
    0 = 表里已有可用快照（本轮新写入，或拉源失败但上一份仍在）
    1 = 表仍为空（冷启动失败，或源不可达且从未落过库）
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

from app.services.fund_manager_roster import (  # noqa: E402
    run_fund_manager_roster_refresh,
)
from app.services.fund_research_profile_store import (  # noqa: E402
    run_fund_research_profile_refresh,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="整包刷新 fund_research_profile（新浪四张开放式大类表）",
    )
    parser.add_argument(
        "--if-stale",
        action="store_true",
        help="仅在空表或超过 24h TTL 时拉源；默认强制刷当日快照",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        default=None,
        help="把刷新摘要写到该路径（机读）",
    )
    args = parser.parse_args()

    force = not args.if_stale
    print(
        f"[{_now()}] 开始刷新基金规模/经理档案（force={force}）",
        flush=True,
    )
    summary = run_fund_research_profile_refresh(force=force)
    roster = run_fund_manager_roster_refresh(force=force)
    summary["manager_roster"] = roster

    print()
    print(f"  本轮写入          {summary.get('written')}")
    print(f"  表内行数          {summary.get('row_count')}")
    print(f"  快照时点          {summary.get('snapshot_available_at')}")
    print(f"  来源              {summary.get('source')}")
    print()
    print(f"  经理名册写入      {roster.get('written')}")
    print(f"  经理名册行数      {roster.get('row_count')}")
    print(f"  经理名册时点      {roster.get('snapshot_available_at')}")
    print(f"  经理名册来源      {roster.get('source')}")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  摘要已写入 {out_path}")

    if not summary.get("ok"):
        print(
            "\n!! 规模档案表仍为空：新浪四表不可达，且从未落过库。"
            "荐基路径不会再拉源，规模/经理会缺。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
