"""探测各数据源能否批量/单块获取板块 5 日涨跌。"""
from __future__ import annotations

import json
import statistics
import time

import httpx
import requests

from app.config import get_settings
from app.services.akshare_subprocess import fetch_board_daily_kline_series
from app.services.discovery_sector_heat import _rolling_change_percent
from app.services.eastmoney_trends_client import fetch_eastmoney_daily_kline_series
from app.services.index_daily_client import fetch_index_daily_history
from app.services.sector_quote_relay_provider import fetch_daily_kline_via_relay
from app.services.theme_board_snapshot import list_theme_board_universe

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Connection": "close",
}
_COMMON = {
    "po": "1",
    "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2",
    "invt": "2",
}


def ms(s: float) -> float:
    return round(s * 1000, 1)


def probe_eastmoney_clist_5d(board_type: str) -> dict:
    """东财 clist 批量：f3=1d, f109=5d(资金流榜常用), f24/f25=部分榜单 5d/10d。"""
    fs = "m:90 t:3 f:!50" if board_type == "concept" else "m:90 t:2 f:!50"
    params = {
        **_COMMON,
        "pn": "1",
        "pz": "500",
        "fid": "f3",
        "fs": fs,
        "fields": "f12,f14,f3,f109,f24,f25",
    }
    t0 = time.perf_counter()
    last_err = None
    for host in ("push2delay.eastmoney.com", "79.push2.eastmoney.com", "17.push2.eastmoney.com"):
        try:
            with httpx.Client(headers=_HEADERS, timeout=15.0, trust_env=False) as client:
                resp = client.get(f"https://{host}/api/qt/clist/get", params=params)
                resp.raise_for_status()
                data = resp.json().get("data") or {}
                rows = data.get("diff") or []
                elapsed = time.perf_counter() - t0
                with_1d = sum(1 for r in rows if r.get("f3") not in (None, "-"))
                with_f109 = sum(1 for r in rows if r.get("f109") not in (None, "-"))
                with_f24 = sum(1 for r in rows if r.get("f24") not in (None, "-"))
                sample = next((r for r in rows if r.get("f109") not in (None, "-")), rows[0] if rows else {})
                return {
                    "ok": bool(rows),
                    "host": host,
                    "ms": ms(elapsed),
                    "rows": len(rows),
                    "with_1d_f3": with_1d,
                    "with_5d_f109": with_f109,
                    "with_f24": with_f24,
                    "sample": {
                        "name": sample.get("f14"),
                        "f3_1d": sample.get("f3"),
                        "f109_5d": sample.get("f109"),
                        "f24": sample.get("f24"),
                        "f25": sample.get("f25"),
                    },
                }
        except Exception as exc:
            last_err = exc
    return {"ok": False, "error": str(last_err)}


def probe_eastmoney_kline(sector: dict) -> dict:
    t0 = time.perf_counter()
    series = fetch_eastmoney_daily_kline_series(
        sector["secid"],
        source_code=sector.get("source_code"),
        max_days=12,
        timeout=8.0,
        max_retries=1,
    )
    elapsed = time.perf_counter() - t0
    change_5d = _rolling_change_percent(series, days=5) if series else None
    return {
        "ok": bool(series),
        "ms": ms(elapsed),
        "bars": len(series),
        "change_5d": change_5d,
        "label": sector.get("sector_label"),
    }


def probe_akshare_board_hist(sector: dict) -> dict:
    kind = sector.get("board_kind") or "concept"
    if kind == "index":
        return {"ok": False, "skip": "index type"}
    code = sector.get("source_code") or ""
    name = sector.get("sector_label") or ""
    t0 = time.perf_counter()
    series = fetch_board_daily_kline_series(
        kind if kind in {"concept", "industry"} else "concept",
        name,
        source_code=code,
        max_days=12,
    )
    elapsed = time.perf_counter() - t0
    change_5d = _rolling_change_percent(series or [], days=5) if series else None
    return {
        "ok": bool(series),
        "ms": ms(elapsed),
        "bars": len(series or []),
        "change_5d": change_5d,
        "label": name,
    }


def probe_sina_index(sector: dict) -> dict:
    code = sector.get("source_code")
    if sector.get("board_kind") != "index" or not code:
        return {"ok": False, "skip": "not index"}
    t0 = time.perf_counter()
    hist = fetch_index_daily_history(code, trading_days=12)
    elapsed = time.perf_counter() - t0
    if not hist or not hist.get("data"):
        return {"ok": False, "ms": ms(elapsed), "label": sector.get("sector_label")}
    data = hist["data"]
    changes = []
    prior = None
    for row in data[-6:]:
        close = row.get("close")
        if prior and prior > 0 and close:
            changes.append(round((float(close) / prior - 1) * 100, 2))
        prior = float(close) if close else prior
    change_5d = round(sum(changes[-5:]), 2) if len(changes) >= 5 else None
    return {
        "ok": True,
        "ms": ms(elapsed),
        "bars": len(data),
        "change_5d": change_5d,
        "label": sector.get("sector_label"),
    }


def probe_relay(sector: dict) -> dict:
    relay = str(get_settings().sector_quotes_relay_url or "").strip()
    if not relay:
        return {"ok": False, "skip": "relay not configured"}
    t0 = time.perf_counter()
    series = fetch_daily_kline_via_relay(
        sector["secid"],
        source_code=sector.get("source_code"),
        max_days=12,
        timeout_seconds=8.0,
    )
    elapsed = time.perf_counter() - t0
    change_5d = _rolling_change_percent(series, days=5) if series else None
    return {
        "ok": bool(series),
        "ms": ms(elapsed),
        "bars": len(series),
        "change_5d": change_5d,
        "label": sector.get("sector_label"),
        "relay": relay,
    }


def main() -> None:
    universe = list_theme_board_universe()
    # 混合样本：指数 + 概念 + 行业
    samples = []
    for kind in ("index", "concept", "industry"):
        for e in universe:
            if e.get("board_kind") == kind:
                samples.append(e)
                break
    if len(samples) < 3:
        samples = universe[:3]

    print("=== 板块 5d 数据源探测 ===\n")

    for board_type in ("concept", "industry"):
        r = probe_eastmoney_clist_5d(board_type)
        print(f"[批量] 东财 clist/{board_type}: {json.dumps(r, ensure_ascii=False)}")

    print()
    for name, fn in [
        ("东财日K push2delay", probe_eastmoney_kline),
        ("AkShare board hist 子进程", probe_akshare_board_hist),
        ("新浪指数日K", probe_sina_index),
        ("sector-relay 日K", probe_relay),
    ]:
        times: list[float] = []
        results = []
        for sector in samples:
            r = fn(sector)
            results.append(r)
            if r.get("ms"):
                times.append(r["ms"] / 1000)
        med = ms(statistics.median(times)) if times else None
        print(f"[单块×{len(samples)}] {name}: median={med}ms")
        for r in results:
            print(f"    {json.dumps(r, ensure_ascii=False)}")

    print("\n完成。")


if __name__ == "__main__":
    main()
