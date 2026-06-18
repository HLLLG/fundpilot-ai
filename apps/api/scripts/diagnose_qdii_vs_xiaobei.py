#!/usr/bin/env python3
"""对比 FundPilot 美股 QDII 参考涨跌 vs 小倍养基「美股基金涨跌助手」快照。

用法（在 apps/api 目录）::

    python scripts/diagnose_qdii_vs_xiaobei.py
    python scripts/diagnose_qdii_vs_xiaobei.py --pretty
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

# 小倍 image1 参考快照（2026-06-17 前后，盘后/夜盘口径）
XIAOBEI_TOP = {
    "NASDAQ_FUT": -1.34,
    "SP500_FUT": -1.21,
    "DOW_FUT": -0.98,
    "FX": -0.02,
}

XIAOBEI_FUNDS: dict[str, tuple[str, float]] = {
    "012920": ("易方达全球成长精选", 0.44),
    "270023": ("广发全球精选股票", 0.49),
    "005698": ("华夏全球科技先锋", 0.31),
    "539002": ("建信新兴市场优选", 1.21),
    "017436": ("华宝纳斯达克精选", -1.43),
    "016701": ("银华海外数字经济", -0.73),
    "001668": ("汇添富全球移动互联", -0.95),
    "006555": ("浦银安盛全球智能科技", 0.02),
    "002891": ("华夏移动互联", -0.48),
    "006373": ("国富全球科技互联", 0.11),
    "000043": ("嘉实美国成长", -0.94),
    "017730": ("嘉实全球产业升级", 1.41),
    "016664": ("天弘全球高端制造", 1.38),
    "501226": ("长城全球新能源车", 0.27),
    "017144": ("华宝海外新能源汽车", -0.79),
}


def _method(estimate_basis: str | None) -> str:
    if estimate_basis and "天天基金" in estimate_basis:
        return "fundgz"
    if estimate_basis and "穿透" in estimate_basis:
        return "holdings"
    if estimate_basis:
        return "index_factor"
    return "none"


def run(*, force_refresh: bool = True) -> dict:
    from app.services.us_market_service import get_us_market_snapshot
    from app.services.us_qdii_quote_policy import quote_mode_for_session

    snap = get_us_market_snapshot(force_refresh=force_refresh)
    payload = snap.model_dump(mode="json")

    top_rows = []
    for quote in snap.futures:
        xb = XIAOBEI_TOP.get(quote.symbol)
        ours = quote.change_percent
        top_rows.append(
            {
                "symbol": quote.symbol,
                "name": quote.display_name,
                "ours": ours,
                "xiaobei": xb,
                "delta": round(ours - xb, 2) if ours is not None and xb is not None else None,
                "status": quote.status,
                "quote_caliber": quote.quote_caliber,
            }
        )
    fx = snap.usd_cny
    top_rows.append(
        {
            "symbol": "FX",
            "name": "汇率",
            "ours": fx.change_percent,
            "xiaobei": XIAOBEI_TOP["FX"],
            "delta": round(fx.change_percent - XIAOBEI_TOP["FX"], 2)
            if fx.change_percent is not None
            else None,
            "status": fx.status,
        }
    )

    fund_rows = []
    fundgz_n = 0
    holdings_n = 0
    factor_n = 0
    none_n = 0
    deltas: list[float] = []

    for item in snap.qdii:
        code = item.fund_code
        xb_name, xb_val = XIAOBEI_FUNDS.get(code, (item.fund_name, None))
        ours = item.reference_change_percent
        method = _method(item.estimate_basis)
        if method == "fundgz":
            fundgz_n += 1
        elif method == "holdings":
            holdings_n += 1
        elif method == "index_factor":
            factor_n += 1
        else:
            none_n += 1
        delta = round(ours - xb_val, 2) if ours is not None and xb_val is not None else None
        if delta is not None:
            deltas.append(abs(delta))
        fund_rows.append(
            {
                "fund_code": code,
                "fund_name": item.fund_name,
                "ours": ours,
                "xiaobei": xb_val,
                "delta": delta,
                "method": method,
                "estimate_basis": item.estimate_basis,
                "tracking_symbol": item.tracking_symbol,
            }
        )

    fund_rows.sort(key=lambda r: abs(r["delta"]) if r["delta"] is not None else 999, reverse=True)

    return {
        "session": {
            "kind": snap.session_kind,
            "label": snap.session_label,
            "et_date": snap.et_date,
            "updated_at": snap.updated_at,
            "quote_mode": quote_mode_for_session(snap.session_kind),
        },
        "status": {
            "futures": snap.futures_status,
            "forex": snap.forex_status,
            "qdii": snap.qdii_status,
            "available": snap.available,
            "stale": snap.stale,
            "message": snap.message,
        },
        "top_metrics": top_rows,
        "qdii_funds": fund_rows,
        "summary": {
            "fund_count": len(fund_rows),
            "fundgz_method": fundgz_n,
            "holdings_method": holdings_n,
            "index_factor_method": factor_n,
            "no_reference": none_n,
            "mean_abs_delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
            "max_abs_delta": round(max(deltas), 2) if deltas else None,
            "within_0_3pct": sum(1 for d in deltas if d <= 0.3),
            "within_0_5pct": sum(1 for d in deltas if d <= 0.5),
            "within_1_0pct": sum(1 for d in deltas if d <= 1.0),
            "direction_match": sum(
                1
                for r in fund_rows
                if r["ours"] is not None
                and r["xiaobei"] is not None
                and (r["ours"] >= 0) == (r["xiaobei"] >= 0)
            ),
        },
        "raw_snapshot_keys": list(payload.keys()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="QDII reference vs 小倍 benchmark")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--no-refresh", action="store_true")
    args = parser.parse_args()

    report = run(force_refresh=not args.no_refresh)
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
