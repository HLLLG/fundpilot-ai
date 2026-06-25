from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import date

from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_FLOW_TIMEOUT_SECONDS = 20
_CACHE_VERSION = "v1"
_LIVE_TTL_SECONDS = 1800.0
_CLOSED_TTL_SECONDS = 3600.0
_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
    "trading_day_pre_open",
}


def _flow_cache_ttl_seconds() -> float:
    session_kind = str(build_trading_session().get("session_kind") or "")
    if session_kind in _INTRADAY_SESSIONS:
        return _LIVE_TTL_SECONDS
    return _CLOSED_TTL_SECONDS


def _northbound_cache_key(trade_date: str) -> str:
    return f"market:northbound:{_CACHE_VERSION}:{trade_date[:10]}"


def fetch_northbound_flow_summary(trade_date: str | None = None) -> dict | None:
    """沪深港通资金流向摘要（亿元），失败返回 None。"""
    anchor = (trade_date or date.today().isoformat())[:10]
    cache_key = _northbound_cache_key(anchor)
    cached = get_spot_snapshot(cache_key, ttl_seconds=_flow_cache_ttl_seconds())
    if cached:
        return dict(cached)

    result = _fetch_northbound_flow_summary_uncached(anchor)
    if result:
        save_spot_snapshot(cache_key, result)
        return result

    stale = get_spot_snapshot_any_age(cache_key)
    return dict(stale) if stale else None


def _fetch_northbound_flow_summary_uncached(anchor: str) -> dict | None:
    script = """
import akshare as ak
import json
try:
    frame = ak.stock_hsgt_fund_flow_summary_em()
    if frame is None or frame.empty:
        print(json.dumps({"error": "empty"}))
    else:
        rows = []
        for _, row in frame.iterrows():
            rows.append({str(k): (None if v != v else str(v)) for k, v in row.items()})
        print(json.dumps({"rows": rows}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_FLOW_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        payload = json.loads(result.stdout.strip())
        if payload.get("error"):
            return None
        return _parse_northbound_summary(payload.get("rows") or [], anchor)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.debug("northbound flow fetch failed: %s", exc)
        return None


def build_market_flow_context(trade_date: str | None = None) -> dict:
    summary = fetch_northbound_flow_summary(trade_date)
    if summary is None:
        return {
            "available": False,
            "message": "北向资金数据暂不可用，战术判断请依赖板块涨跌与分时。",
        }
    return {
        "available": True,
        "trade_date": summary.get("trade_date"),
        "northbound_net_yi": summary.get("northbound_net_yi"),
        "southbound_net_yi": summary.get("southbound_net_yi"),
        "interpretation": summary.get("interpretation"),
    }


def _parse_northbound_summary(rows: list[dict], anchor: str) -> dict | None:
    if not rows:
        return None

    trade_date = _pick_text(rows[-1], "交易日", "日期", "trade_date") or anchor
    north = _sum_flow_yi(rows, direction="北向", boards={"沪股通", "深股通"})
    south = _sum_flow_yi(rows, direction="南向", boards={"港股通(沪)", "港股通(深)", "沪港通", "深港通"})

    if north is None:
        latest = rows[-1]
        north = _pick_float(latest, "北向资金", "成交净买额", "资金净流入")

    interpretation = "北向资金中性，短线参考权重一般。"
    if north is not None:
        if north >= 50:
            interpretation = f"北向净流入约 {north:.0f} 亿，偏利好风险偏好与成长板块短线动能。"
        elif north <= -30:
            interpretation = f"北向净流出约 {abs(north):.0f} 亿，战术追涨需更谨慎。"

    return {
        "trade_date": trade_date[:10] if trade_date else anchor,
        "northbound_net_yi": round(north, 2) if north is not None else None,
        "southbound_net_yi": round(south, 2) if south is not None else None,
        "interpretation": interpretation,
    }


def _sum_flow_yi(
    rows: list[dict],
    *,
    direction: str,
    boards: set[str],
) -> float | None:
    total = 0.0
    matched = False
    for row in rows:
        flow_dir = _pick_text(row, "资金方向") or ""
        board = _pick_text(row, "板块") or ""
        if direction not in flow_dir:
            continue
        if board not in boards and not any(token in board for token in boards):
            continue
        amount = _pick_float(row, "成交净买额", "资金净流入")
        if amount is None:
            continue
        total += amount
        matched = True
    return total if matched else None


def _pick_float(row: dict, *keys: str) -> float | None:
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if value is None:
            continue
        text = str(value).replace(",", "").replace("亿", "").strip()
        if not text or text.lower() == "nan":
            continue
        try:
            return float(text)
        except ValueError:
            continue
    for value in row.values():
        if value is None:
            continue
        text = str(value).replace(",", "").strip()
        if text.endswith("亿") and _looks_numeric(text[:-1]):
            try:
                return float(text[:-1])
            except ValueError:
                continue
    return None


def _pick_text(row: dict, *keys: str) -> str | None:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return str(row[key]).strip()
    return None


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False
