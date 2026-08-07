#!/usr/bin/env python3
"""把「我们相信的指数身份」与东财实时数据对账。

背景：板块名 → 行情标的的映射写错了不会报错，只会让页面上的涨跌幅静默变成另一只
指数的涨跌幅。2026-08 的一次全表人工对账在 THEME_BOARD_INDEX 里查出 22 条错配
（军工指向沪深300USD、有色金属指向800汽车、机器人指向卫星产业……）。那次是靠人
逐条比对东财实时名称发现的——离线单测查不出来，因为表内自洽并不代表表与市场一致。

这个脚本把那次人工动作固化成可重复执行的对账，覆盖三层：

  cache    var/amac/em_index_lookup.json（离线重算 AMAC 库时的名→码输入）
           与实时指数全集比对。**只告警不失败**：这张表只在离线重建时读，
           运行时不参与取数。
  library  app/data/amac_benchmark_index_library.json 里每条记下的
           eastmoney_name 与实时按 secid 取回的名称比对。**不一致即失败**：
           说明代码解析当时就错了，或指数被改名/退役。
  registry THEME_BOARD_INDEX 每个 label 的 secid → 实时名称，与提交在库里的
           基线 app/data/sector_quote_identity_baseline.json 比对。**变动即失败**，
           必须人工看过再 --write-baseline 更新。另外跑一遍
           THEME_BOARD_PROVIDER_IDENTITIES 的严格策略。

用法（在 apps/api 下）：
    python scripts/reconcile_em_index_lookup.py                     # 全量对账
    python scripts/reconcile_em_index_lookup.py --check registry
    python scripts/reconcile_em_index_lookup.py --write-baseline    # 人工复核后固化
    python scripts/reconcile_em_index_lookup.py --refresh-cache     # 刷新离线输入
    python scripts/reconcile_em_index_lookup.py --json var/reconcile.json

退出码：0 = 无失败项；1 = 有失败项（可直接用于定时任务/CI 门槛）；2 = 取数失败。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

CACHE_PATH = API_ROOT / "var" / "amac" / "em_index_lookup.json"
LIBRARY_PATH = API_ROOT / "app" / "data" / "amac_benchmark_index_library.json"
BASELINE_PATH = API_ROOT / "app" / "data" / "sector_quote_identity_baseline.json"

# 东财指数全集的三个分区：中证/其它(m:2)、沪市指数(m:1+t:1)、深市指数(m:0+t:5)。
_CLIST_PARTITIONS = ("m:2", "m:1+t:1", "m:0+t:5")


def _load_sync_module():
    """按路径加载同目录的构建脚本（scripts/ 不是包，不能 import）。"""
    import importlib.util

    path = Path(__file__).with_name("sync_amac_benchmark_index_library.py")
    spec = importlib.util.spec_from_file_location("_fp_sync_amac", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 结构性错误
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 身份归一化与构建脚本共用同一个定义。两边判的是同一件事——构建期判"手写简称与
# 实况相符"、对账期判"库里记的名称与实况相符"——各写一份必然漂移，门槛就变成噪音。
_norm_security_name = _load_sync_module().norm_security_name


# --------------------------------------------------------------------- 取数
def _fetch_live_universe() -> dict[str, str]:
    """东财指数全集 code → 简称。复用仓库自己的 transport（host 池 + 免代理）。"""
    from app.services.eastmoney_http import eastmoney_requests_client
    from app.services.eastmoney_spot_client import (
        _CLIST_HOSTS,
        _EASTMONEY_HEADERS,
    )

    session = eastmoney_requests_client(_EASTMONEY_HEADERS)
    universe: dict[str, str] = {}

    def page(fs: str, pn: int) -> list[dict]:
        params = {
            "np": "1",
            "fltt": "1",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": "f12,f14",
            "pn": str(pn),
            "pz": "100",
            "po": "1",
            "dect": "1",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        last: Exception | None = None
        for host in _CLIST_HOSTS:
            for attempt in range(3):
                try:
                    response = session.get(
                        f"https://{host}/api/qt/clist/get",
                        params=params,
                        headers=_EASTMONEY_HEADERS,
                        timeout=25,
                    )
                    response.raise_for_status()
                    return response.json().get("data", {}).get("diff") or []
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"clist 取数失败 fs={fs} pn={pn}: {last}")

    for fs in _CLIST_PARTITIONS:
        pn = 1
        while True:
            batch = page(fs, pn)
            if not batch:
                break
            for row in batch:
                code = str(row.get("f12", "")).strip()
                name = str(row.get("f14", "")).strip()
                if code and name:
                    universe.setdefault(code, name)
            if len(batch) < 100:
                break
            pn += 1
            time.sleep(0.15)
    return universe


def _fetch_names_by_secid(secids: list[str], *, workers: int = 6) -> dict[str, str | None]:
    """secid → 实时证券简称。stock/get 是权威口径（会校验回包的 code/market）。"""
    from app.services.eastmoney_spot_client import fetch_eastmoney_quote_by_secid

    def one(secid: str) -> tuple[str, str | None]:
        try:
            name, _pct = fetch_eastmoney_quote_by_secid(secid, timeout=8.0, max_retries=2)
        except Exception:  # noqa: BLE001
            return secid, None
        return secid, (name or None)

    unique = list(dict.fromkeys(s for s in secids if s))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(unique) or 1))) as pool:
        return dict(pool.map(one, unique))


# ------------------------------------------------------------------ 各层对账
def _check_cache(live: dict[str, str], *, refresh: bool) -> dict:
    if not CACHE_PATH.is_file():
        return {"status": "skipped", "reason": f"{CACHE_PATH} 不存在"}

    cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if not isinstance(cached, dict):
        return {"status": "skipped", "reason": "缓存不是 code→name 字典"}

    renamed, vanished = [], []
    for code, name in sorted(cached.items()):
        live_name = live.get(str(code))
        if live_name is None:
            vanished.append({"code": code, "cached_name": name})
        elif live_name != name:
            renamed.append({"code": code, "cached_name": name, "live_name": live_name})

    added = sorted(set(live) - {str(c) for c in cached})
    result = {
        # 缓存只是离线重建的输入，漂移不阻断，但必须看得见。
        "status": "warn" if (renamed or vanished) else "ok",
        "cached_total": len(cached),
        "live_total": len(live),
        "renamed": renamed,
        "vanished": vanished,
        "added_count": len(added),
        "added_sample": added[:20],
    }

    if refresh:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(live, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result["refreshed"] = str(CACHE_PATH)
    return result


def _check_library() -> dict:
    if not LIBRARY_PATH.is_file():
        return {"status": "skipped", "reason": f"{LIBRARY_PATH} 不存在"}

    library = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    entries = library.get("entries", []) if isinstance(library, dict) else library

    checkable = [
        entry
        for entry in entries
        if entry.get("eastmoney_secid") and entry.get("eastmoney_name")
    ]
    live_names = _fetch_names_by_secid([e["eastmoney_secid"] for e in checkable])

    mismatched, unreachable = [], []
    for entry in checkable:
        secid = entry["eastmoney_secid"]
        expected = str(entry["eastmoney_name"])
        actual = live_names.get(secid)
        row = {
            "index_full_name": entry.get("index_full_name"),
            "source_code": entry.get("source_code"),
            "secid": secid,
            "stored_name": expected,
            "live_name": actual,
            "theme_label": entry.get("theme_label"),
            "resolution": entry.get("resolution"),
        }
        if actual is None:
            unreachable.append(row)
        elif _norm_security_name(actual) != _norm_security_name(expected):
            mismatched.append(row)

    return {
        "status": "fail" if mismatched else ("warn" if unreachable else "ok"),
        "entries_total": len(entries),
        "checked": len(checkable),
        "mismatched": mismatched,
        "unreachable": unreachable,
    }


def _check_registry(*, write_baseline: bool) -> dict:
    from app.services.sector_quote_identity import (
        provider_identity_matches,
        requires_provider_identity_check,
    )
    from app.services.sector_registry_data import THEME_BOARD_INDEX

    specs = {
        label: {"secid": spec[0], "source_code": spec[1], "source_type": spec[2]}
        for label, spec in THEME_BOARD_INDEX.items()
        if spec and spec[0]
    }
    live_names = _fetch_names_by_secid([s["secid"] for s in specs.values()])

    observed: dict[str, dict[str, str | None]] = {}
    unreachable = []
    for label, spec in sorted(specs.items()):
        actual = live_names.get(spec["secid"])
        observed[label] = {
            "secid": spec["secid"],
            "source_code": spec["source_code"],
            "live_name": actual,
        }
        if actual is None:
            unreachable.append({"label": label, **spec})

    # 严格身份策略：注册表里显式声明过期望名称的 label 必须当场通过。
    identity_failures = []
    for label, spec in sorted(specs.items()):
        if not requires_provider_identity_check(label):
            continue
        actual = live_names.get(spec["secid"])
        if not provider_identity_matches(
            label,
            expected_source_code=spec["source_code"],
            actual_security_name=actual,
            actual_security_code=spec["source_code"],
        ):
            identity_failures.append(
                {"label": label, "secid": spec["secid"], "live_name": actual}
            )

    baseline_raw = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        if BASELINE_PATH.is_file()
        else {}
    )
    baseline = baseline_raw.get("labels", {}) if isinstance(baseline_raw, dict) else {}

    changed, new_labels, dropped = [], [], []
    for label, got in observed.items():
        was = baseline.get(label)
        if was is None:
            new_labels.append({"label": label, **got})
        elif (
            was.get("secid") != got["secid"]
            or was.get("source_code") != got["source_code"]
            or (
                got["live_name"] is not None
                and _norm_security_name(was.get("live_name"))
                != _norm_security_name(got["live_name"])
            )
        ):
            changed.append({"label": label, "baseline": was, "observed": got})
    for label in baseline:
        if label not in observed:
            dropped.append(label)

    has_baseline = bool(baseline)
    failed = bool(identity_failures or changed or (has_baseline and new_labels))
    result = {
        "status": "fail" if failed else ("warn" if unreachable or not has_baseline else "ok"),
        "labels_total": len(specs),
        "reachable": sum(1 for v in observed.values() if v["live_name"]),
        "identity_failures": identity_failures,
        "unreachable": unreachable,
        "baseline_present": has_baseline,
        "changed": changed,
        "new": new_labels,
        "dropped": dropped,
    }

    if write_baseline:
        # 只固化取到名称的条目：把 None 写进基线等于把"取数失败"当成事实。
        keep = {k: v for k, v in observed.items() if v["live_name"]}
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "note": (
                        "THEME_BOARD_INDEX 各 label 行情标的的实时名称基线。"
                        "由 scripts/reconcile_em_index_lookup.py --write-baseline 生成，"
                        "变动必须人工复核后再更新。"
                    ),
                    "labels": keep,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        result["baseline_written"] = str(BASELINE_PATH)
        result["baseline_written_count"] = len(keep)
        result["baseline_skipped_unreachable"] = len(observed) - len(keep)
    return result


# --------------------------------------------------------------------- 输出
def _emit(report: dict) -> None:
    order = ("cache", "library", "registry")
    icon = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "skipped": "SKIP"}
    for name in order:
        section = report.get(name)
        if not section:
            continue
        print(f"[{icon.get(section['status'], '????')}] {name}")
        if section["status"] == "skipped":
            print(f"        {section.get('reason')}")
            continue

        if name == "cache":
            print(
                f"        cached={section['cached_total']} live={section['live_total']} "
                f"renamed={len(section['renamed'])} vanished={len(section['vanished'])} "
                f"new={section['added_count']}"
            )
            for row in section["renamed"][:40]:
                print(
                    f"        renamed  {row['code']:<10} "
                    f"{row['cached_name']} -> {row['live_name']}"
                )
            for row in section["vanished"][:40]:
                print(f"        vanished {row['code']:<10} {row['cached_name']}")

        elif name == "library":
            print(
                f"        entries={section['entries_total']} checked={section['checked']} "
                f"mismatched={len(section['mismatched'])} "
                f"unreachable={len(section['unreachable'])}"
            )
            for row in section["mismatched"]:
                print(
                    f"        MISMATCH {row['source_code']:<10} {row['secid']:<12} "
                    f"stored={row['stored_name']} live={row['live_name']} "
                    f"({row['index_full_name']}, label={row['theme_label']})"
                )
            for row in section["unreachable"][:20]:
                print(
                    f"        no-quote {row['source_code']:<10} {row['secid']:<12} "
                    f"{row['index_full_name']}"
                )

        elif name == "registry":
            print(
                f"        labels={section['labels_total']} "
                f"reachable={section['reachable']} "
                f"baseline={'yes' if section['baseline_present'] else 'MISSING'} "
                f"changed={len(section['changed'])} new={len(section['new'])} "
                f"identity_failures={len(section['identity_failures'])}"
            )
            for row in section["identity_failures"]:
                print(
                    f"        IDENTITY {row['label']:<12} {row['secid']:<12} "
                    f"live={row['live_name']}"
                )
            for row in section["changed"]:
                print(
                    f"        CHANGED  {row['label']:<12} "
                    f"{row['baseline'].get('secid')}/{row['baseline'].get('live_name')} "
                    f"-> {row['observed']['secid']}/{row['observed']['live_name']}"
                )
            for row in section["new"][:40]:
                print(
                    f"        NEW      {row['label']:<12} {row['secid']:<12} "
                    f"{row['live_name']}"
                )
            for row in section["unreachable"][:20]:
                print(f"        no-quote {row['label']:<12} {row['secid']}")
            if section.get("baseline_written"):
                print(
                    f"        baseline written: {section['baseline_written_count']} labels "
                    f"-> {section['baseline_written']}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        choices=("all", "cache", "library", "registry"),
        default="all",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="用实时全集重写 var/amac/em_index_lookup.json",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="人工复核后固化 THEME_BOARD_INDEX 实时名称基线",
    )
    parser.add_argument("--json", dest="json_path", help="同时写一份机器可读报告")
    args = parser.parse_args()

    wants = (
        {"cache", "library", "registry"} if args.check == "all" else {args.check}
    )
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}

    if "cache" in wants:
        try:
            live = _fetch_live_universe()
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] 无法取到东财指数全集: {exc}", file=sys.stderr)
            return 2
        report["cache"] = _check_cache(live, refresh=args.refresh_cache)

    if "library" in wants:
        report["library"] = _check_library()

    if "registry" in wants:
        report["registry"] = _check_registry(write_baseline=args.write_baseline)

    _emit(report)

    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {out}")

    failed = [
        name
        for name in ("cache", "library", "registry")
        if report.get(name, {}).get("status") == "fail"
    ]
    if failed:
        print(f"\n对账失败: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
