#!/usr/bin/env python3
"""失效那些用旧规则解析出来、按新身份门槛已站不住的基准派生关联板块。

背景：`fund_sector_current` / `fund_primary_sectors_global` 里由业绩基准链路写入的
板块，是用当时的解析规则算出来的。2026-08 修掉了三类错误（AMAC 主题标签错配、
东财同名指数代码歧义、缺少身份一致性校验）之后，一部分存量行的板块已经不成立，
但它们的 TTL 还没到期，不会自动重算。

做法刻意保守：
  * **不改写** 板块名，只删除派生缓存行，让后台 precompute 用新规则重算；
  * 判断依据是把每行 detail 里存的 benchmark_text 重新跑一遍解析，与存量板块比对，
    而不是靠猜"哪些板块受影响"；
  * 手工/OCR 沉淀的板块（source 为 manual / ocr_detail）一律跳过；
  * `fund_sector_exposure_snapshots` 是追加式 PIT 证据历史，不删。

删除 `fund_sector_resolution_status` 里对应行是让重算尽快发生的必要动作：
`_bulk_resolution_candidates` 把"没有状态行"的基金排在最高优先级，而
`queued` 状态反而**不**在候选列表里。

用法（在 apps/api 下）：
    python scripts/invalidate_stale_benchmark_sectors.py            # 只报告
    python scripts/invalidate_stale_benchmark_sectors.py --apply    # 实际执行
    python scripts/invalidate_stale_benchmark_sectors.py --apply --no-backup
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

_BENCHMARK_SOURCES = ("benchmark_index", "precompute_benchmark")
_HOLDINGS_SOURCES = ("holdings_infer", "precompute_holdings")
_PROTECTED_SOURCES = frozenset({"manual", "ocr_detail"})


def _load_detail(raw: object) -> dict:
    if not raw:
        return {}
    try:
        decoded = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _classify(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    """按新规则重放每行的基准文本，划分 unchanged / relabeled / unresolvable。"""
    from app.services.fund_benchmark_sector import resolve_sector_from_benchmark

    placeholders = ",".join("?" * len(_BENCHMARK_SOURCES))
    rows = connection.execute(
        f"""SELECT fund_code, sector_name, source, source_ref, detail
            FROM fund_sector_current
            WHERE is_primary = 1 AND source IN ({placeholders})
            ORDER BY fund_code""",
        _BENCHMARK_SOURCES,
    ).fetchall()

    buckets: dict[str, list[dict]] = {
        "unchanged": [],
        "relabeled": [],
        "recoded": [],
        "unresolvable": [],
        "no_benchmark_text": [],
    }
    for row in rows:
        detail = _load_detail(row["detail"])
        benchmark_text = str(detail.get("benchmark_text") or "").strip()
        item = {
            "fund_code": row["fund_code"],
            "stored_sector": row["sector_name"],
            "stored_code": (row["source_ref"] or "").upper(),
            "benchmark_text": benchmark_text,
            "index_name": detail.get("index_name"),
        }
        if not benchmark_text:
            buckets["no_benchmark_text"].append(item)
            continue
        resolved = resolve_sector_from_benchmark(benchmark_text)
        if resolved is None:
            item["new_sector"] = None
            item["new_code"] = None
            buckets["unresolvable"].append(item)
            continue
        new_sector, _intraday, match = resolved
        item["new_sector"] = new_sector
        item["new_code"] = match.index_code
        if new_sector != row["sector_name"]:
            buckets["relabeled"].append(item)
        elif (match.index_code or "").upper() != item["stored_code"]:
            # 板块名没变，但记录的跟踪指数代码变了（注册表换了同名正确指数）。
            # 展示的涨跌幅本来就按 label 实时解析，所以用户看不出差别；失效的目的是
            # 让 source_ref 这条溯源信息与现在的解析结果一致，避免后续复算判定不可复现。
            buckets["recoded"].append(item)
        else:
            buckets["unchanged"].append(item)
    return buckets


def _classify_holdings(
    connection: sqlite3.Connection,
    *,
    verified_only: bool = False,
) -> dict[str, list[dict]]:
    """按当前规则重放持仓链路的主板块。

    走**股票级**证据，不是把已归并的板块名再折叠一遍。后者曾是本函数的实现，
    看着等价其实会漏判：`detail` 里存的 `sector_name` 已经是归并结果，一旦归并
    规则在股票那一层改了（如 军工电子Ⅱ 从"电子"改到"军工"），从"电子"这个名字
    再也还原不回 军工电子Ⅱ，于是该基金被判成"主板块不变"而逃过失效。
    实测 015945 易方达国防军工就是这种情况：按板块名重放得"电子"（不变），
    按股票行业重放得"军工"（该失效）。

    `detail.evidence` 每条带原始 `industry` 与 `weight`，直接喂给生产函数
    `assess_sector_from_portfolio_stocks`，dry-run 的结论才与真实重算一致。
    证据缺失（老版本写入的行）才退回板块名折叠。
    """
    from app.services.fund_holdings_sector_infer import (
        HoldingStockRow,
        assess_sector_from_portfolio_stocks,
    )
    from app.services.fund_industry_theme_map import map_industry_to_theme_label

    placeholders = ",".join("?" * len(_HOLDINGS_SOURCES))
    rows = connection.execute(
        f"""SELECT fund_code, sector_name, exposure_percent, is_primary,
                   identity_status, detail
            FROM fund_sector_current
            WHERE source IN ({placeholders})
            ORDER BY fund_code""",
        _HOLDINGS_SOURCES,
    ).fetchall()

    by_fund: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_fund.setdefault(
            row["fund_code"],
            {"primary": None, "status": None, "exposures": {}, "stocks": {}},
        )
        try:
            exposure = float(row["exposure_percent"])
        except (TypeError, ValueError):
            exposure = 0.0
        if row["is_primary"]:
            entry["primary"] = row["sector_name"]
            entry["status"] = row["identity_status"]
        entry["exposures"][row["sector_name"]] = exposure

        # `_row_detail` 按板块过滤 evidence 并截断到 8 条。季报只披露前十大，
        # 单个板块极少超过 8 只，所以把该基金各行的 evidence 取并集通常就是全集；
        # 按 stock_code 去重避免同一只股票被重复计权。
        detail = _load_detail(row["detail"])
        for item in detail.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            try:
                weight = float(item.get("weight"))
            except (TypeError, ValueError):
                continue
            key = str(item.get("stock_code") or item.get("stock") or "")
            if not key:
                continue
            entry["stocks"][key] = HoldingStockRow(
                name=str(item.get("stock") or ""),
                weight=weight,
                industry=item.get("industry"),
                stock_code=item.get("stock_code"),
                coverage=item.get("coverage"),
                industry_pit_qualified=bool(item.get("industry_pit_qualified")),
                theme=item.get("refined_theme") or None,
                theme_pit_qualified=bool(item.get("theme_pit_qualified")),
            )

    out: dict[str, list[dict]] = {"unchanged": [], "relabeled": [], "unclassifiable": []}
    for fund_code, entry in by_fund.items():
        stored = entry["primary"]
        if not stored:
            continue
        if verified_only and entry["status"] != "verified":
            # pending 行只是研究线索，不会成为持仓页展示的板块；重算它们要为每只股票
            # 联网取行业分类，代价远大于收益，交给 TTL 自然刷新。
            continue

        stocks = list(entry["stocks"].values())
        if stocks:
            assessed = assess_sector_from_portfolio_stocks(stocks)
            new_sector = assessed.get("sector_name")
            replay = "stock_industry"
        else:
            folded: dict[str, float] = {}
            for label, exposure in entry["exposures"].items():
                mapped = map_industry_to_theme_label(label)
                if mapped is None:
                    continue
                folded[mapped] = folded.get(mapped, 0.0) + exposure
            # 与生产路径同一个确定性裁决：权重降序、同权重取 label 升序。
            new_sector = (
                min(folded, key=lambda key: (-folded[key], key)) if folded else None
            )
            replay = "sector_name_refold"

        item = {
            "fund_code": fund_code,
            "stored_sector": stored,
            "new_sector": new_sector,
            "replay": replay,
        }
        if new_sector is None:
            out["unclassifiable"].append(item)
        elif new_sector == stored:
            out["unchanged"].append(item)
        else:
            out["relabeled"].append(item)
    return out


def _report_holdings(buckets: dict[str, list[dict]]) -> list[str]:
    stale = [*buckets["relabeled"], *buckets["unclassifiable"]]
    codes = sorted({item["fund_code"] for item in stale})

    print("=" * 96)
    print("持仓穿透主板块重放结果（按新的行业→板块归并规则）")
    print("=" * 96)
    for name, label in (
        ("unchanged", "主板块不变（保留）"),
        ("relabeled", "主板块改变（失效重算）"),
        ("unclassifiable", "所有板块都归并不到可交易板块（失效，重算后为暂无板块）"),
    ):
        print(f"  {label:<48} {len(buckets[name]):>5}")

    if buckets["relabeled"]:
        print()
        print("-" * 96)
        print("主板块变化（旧 → 新）")
        print("-" * 96)
        pairs = Counter(
            (item["stored_sector"], item["new_sector"]) for item in buckets["relabeled"]
        )
        for (old, new), count in pairs.most_common(40):
            print(f"   {str(old):<14} -> {str(new):<14} {count:>5} 只")

    if buckets["unclassifiable"]:
        print()
        print("-" * 96)
        print("无可交易板块（旧板块汇总）")
        print("-" * 96)
        for old, count in Counter(
            item["stored_sector"] for item in buckets["unclassifiable"]
        ).most_common(30):
            print(f"   {str(old):<14} {count:>5} 只")

    # 重放路径必须看得见：stock_industry 是按股票级行业走生产函数（可信），
    # sector_name_refold 是证据缺失时退回板块名折叠（会漏判股票层的规则变化）。
    replays = Counter(
        item.get("replay") or "unknown"
        for name in ("unchanged", "relabeled", "unclassifiable")
        for item in buckets[name]
    )
    print()
    print(f"重放路径分布: {dict(replays)}")
    print(f"待失效基金数: {len(codes)}")
    return codes


def _report(buckets: dict[str, list[dict]]) -> list[str]:
    stale = [*buckets["relabeled"], *buckets["recoded"], *buckets["unresolvable"]]
    codes = sorted({item["fund_code"] for item in stale})

    print("=" * 96)
    print("基准派生主板块重放结果")
    print("=" * 96)
    for name, label in (
        ("unchanged", "板块与跟踪码都不变（保留）"),
        ("relabeled", "板块改变（失效重算）"),
        ("recoded", "板块不变但跟踪码变了（失效，仅刷新溯源）"),
        ("unresolvable", "新规则下无法核验身份（失效，重算后可能变成暂无板块）"),
        ("no_benchmark_text", "detail 里没有基准原文（保留，无法重放）"),
    ):
        print(f"  {label:<44} {len(buckets[name]):>5}")

    if buckets["recoded"]:
        print()
        print("-" * 96)
        print("跟踪码变化（按 板块 + 旧码 → 新码 汇总）")
        print("-" * 96)
        recoded = Counter(
            (item["stored_sector"], item["stored_code"], item["new_code"])
            for item in buckets["recoded"]
        )
        for (sector, old, new), count in recoded.most_common():
            print(f"   {sector:<10} {old:<10} -> {new:<10} {count:>4} 只")

    if buckets["relabeled"]:
        print()
        print("-" * 96)
        print("板块改变明细（按 旧板块 → 新板块 汇总）")
        print("-" * 96)
        pairs = Counter(
            (item["stored_sector"], item["new_sector"]) for item in buckets["relabeled"]
        )
        for (old, new), count in pairs.most_common():
            print(f"   {old:<10} -> {new:<10} {count:>4} 只")

    if buckets["unresolvable"]:
        print()
        print("-" * 96)
        print("失去身份明细（按 旧板块 + 跟踪指数 汇总）")
        print("-" * 96)
        groups = Counter(
            (item["stored_sector"], item["stored_code"], str(item["index_name"]))
            for item in buckets["unresolvable"]
        )
        for (old, code, index_name), count in groups.most_common():
            print(f"   {old:<10} tracked={code:<10} {index_name:<28} {count:>4} 只")

    print()
    print(f"待失效基金数: {len(codes)}")
    return codes


def _apply(connection: sqlite3.Connection, codes: list[str]) -> dict[str, int]:
    deleted: dict[str, int] = {}
    chunk = 400
    for table, extra_where, params_extra in (
        ("fund_sector_current", "", ()),
        ("fund_primary_sectors_global", "", ()),
        (
            "fund_primary_sectors",
            f" AND source NOT IN ({','.join('?' * len(_PROTECTED_SOURCES))})",
            tuple(sorted(_PROTECTED_SOURCES)),
        ),
        ("fund_sector_resolution_status", "", ()),
    ):
        total = 0
        for start in range(0, len(codes), chunk):
            batch = codes[start : start + chunk]
            placeholders = ",".join("?" * len(batch))
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE fund_code IN ({placeholders}){extra_where}",
                (*batch, *params_extra),
            )
            total += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        deleted[table] = total
    connection.commit()
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="失效按新身份门槛已站不住的基准派生关联板块"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际删除派生缓存行（默认只报告）",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="--apply 时跳过数据库备份（不建议）",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="覆盖数据库路径（默认取 app.database.database_file_path()）",
    )
    parser.add_argument(
        "--chain",
        choices=("benchmark", "holdings", "all"),
        default="benchmark",
        help="要重放的解析链路（默认只查业绩基准链路）",
    )
    parser.add_argument(
        "--verified-only",
        action="store_true",
        help="持仓链路只处理决策级(verified)行，pending 研究线索交给 TTL 自然刷新",
    )
    args = parser.parse_args()

    from app.database import database_file_path

    db_path = args.db or database_file_path()
    if not db_path.is_file():
        print(f"找不到数据库: {db_path}")
        return 1
    print(f"数据库: {db_path}")

    mode = "" if args.apply else "?mode=ro"
    connection = sqlite3.connect(f"file:{db_path}{mode}", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        codes: list[str] = []
        if args.chain in ("benchmark", "all"):
            codes.extend(_report(_classify(connection)))
        if args.chain in ("holdings", "all"):
            if codes:
                print()
            codes.extend(
                _report_holdings(
                    _classify_holdings(connection, verified_only=args.verified_only)
                )
            )
        codes = sorted(set(codes))
        if args.chain == "all":
            print()
            print(f"两条链路合计待失效基金数: {len(codes)}")

        if not args.apply:
            print()
            print("dry-run：未修改任何数据。加 --apply 执行。")
            return 0
        if not codes:
            print()
            print("没有需要失效的行。")
            return 0

        if not args.no_backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = db_path.with_name(f"{db_path.name}.bak-sector-invalidate-{stamp}")
            shutil.copy2(db_path, backup)
            print()
            print(f"已备份数据库到: {backup}")

        deleted = _apply(connection, codes)
        print()
        print("已删除派生缓存行:")
        for table, count in deleted.items():
            print(f"   {table:<34} {count:>6}")
        print()
        print(
            "后台 fund-primary-sector-precompute 会把这些基金当作 missing 重新解析"
            "（missing 在候选队列里优先级最高）。"
        )
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
