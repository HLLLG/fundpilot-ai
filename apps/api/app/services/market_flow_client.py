from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import date, datetime, timezone

from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_FLOW_TIMEOUT_SECONDS = 20
_CACHE_VERSION = "v4-southbound-only"
_LIVE_TTL_SECONDS = 1800.0
_CLOSED_TTL_SECONDS = 3600.0
_STALE_FALLBACK_MAX_AGE_SECONDS = 6 * 3600.0
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


def _stock_connect_cache_key(trade_date: str) -> str:
    return f"market:stock-connect:{_CACHE_VERSION}:{trade_date[:10]}"


def fetch_stock_connect_flow_summary(trade_date: str | None = None) -> dict | None:
    """按交易日返回南向资金公开摘要（亿元），失败返回 None。"""
    anchor = _resolve_anchor(trade_date)
    cache_key = _stock_connect_cache_key(anchor)
    cached = get_spot_snapshot(cache_key, ttl_seconds=_flow_cache_ttl_seconds())
    if cached:
        safe_cached = _sanitize_summary(cached, anchor)
        if safe_cached is not None and _has_available_flow(safe_cached):
            safe_cached["stale"] = False
            return safe_cached

    result = _fetch_stock_connect_flow_summary_uncached(anchor)
    if result:
        safe_result = _sanitize_summary(result, anchor)
        if safe_result is not None and _has_available_flow(safe_result):
            safe_result["fetched_at"] = _utc_now().isoformat()
            safe_result["stale"] = False
            save_spot_snapshot(cache_key, safe_result)
            return safe_result

    stale = get_spot_snapshot_any_age(cache_key)
    safe_stale = _sanitize_summary(stale, anchor) if stale else None
    if safe_stale is None or not _has_available_flow(safe_stale):
        return None
    if not _stale_fallback_within_boundary(safe_stale):
        return None
    safe_stale["stale"] = True
    return safe_stale


def _fetch_stock_connect_flow_summary_uncached(anchor: str) -> dict | None:
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
            if "南向" not in str(row.get("资金方向") or ""):
                continue
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
        return _parse_stock_connect_summary(payload.get("rows") or [], anchor)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.debug("stock connect flow fetch failed: %s", exc)
        return None


def build_stock_connect_flow_context(trade_date: str | None = None) -> dict:
    anchor = _resolve_anchor(trade_date)
    summary = fetch_stock_connect_flow_summary(trade_date)
    # v4 只接收南向字段；旧版缓存中的其他资金字段不会进入新 facts/LLM payload。
    safe_summary = _sanitize_summary(summary, anchor) if summary else None
    if safe_summary is None or not _has_available_flow(safe_summary):
        return {
            "schema_version": "stock_connect_flow.v2",
            "available": False,
            "reason": "stock_connect_flow_unavailable",
            "trade_date": anchor,
            "southbound_net_yi": None,
            "southbound_available": False,
            "southbound_reason": "source_unavailable_or_trade_date_mismatch",
            "interpretation": _build_interpretation(None),
            "message": "南向资金数据暂未取得，不参与当前判断。",
        }
    return {
        "schema_version": "stock_connect_flow.v2",
        "available": True,
        "trade_date": safe_summary.get("trade_date"),
        "southbound_net_yi": safe_summary.get("southbound_net_yi"),
        "southbound_available": bool(safe_summary.get("southbound_available")),
        "southbound_reason": safe_summary.get("southbound_reason"),
        "interpretation": safe_summary.get("interpretation"),
        "source": safe_summary.get("source"),
        "fetched_at": safe_summary.get("fetched_at"),
        "stale": bool(safe_summary.get("stale")),
        "message": "南向数据仅作为港股资金面的独立参考。",
    }


def _parse_stock_connect_summary(rows: list[dict], anchor: str) -> dict | None:
    if not rows:
        return None

    normalized_anchor = _normalize_trade_date(anchor)
    if normalized_anchor is None:
        return None

    # 不再用 anchor 填补缺失的源日期，也不把其他交易日的行聚合到今天。
    aligned_rows = [
        row
        for row in rows
        if _normalize_trade_date(_pick_text(row, "交易日", "日期", "trade_date"))
        == normalized_anchor
    ]
    if not aligned_rows:
        return None

    south = _sum_flow_yi(
        aligned_rows,
        direction="南向",
        boards={"港股通(沪)", "港股通(深)", "沪港通", "深港通"},
    )

    return {
        "trade_date": normalized_anchor,
        "southbound_net_yi": round(south, 2) if south is not None else None,
        "southbound_available": south is not None,
        "southbound_reason": None if south is not None else "source_value_missing",
        "interpretation": _build_interpretation(south),
        "source": "eastmoney_southbound_summary_via_akshare",
    }


def _sanitize_summary(summary: dict | None, anchor: str) -> dict | None:
    if not isinstance(summary, dict):
        return None

    normalized_anchor = _normalize_trade_date(anchor)
    source_date = _normalize_trade_date(summary.get("trade_date"))
    if normalized_anchor is None or source_date != normalized_anchor:
        return None

    south = _coerce_float(summary.get("southbound_net_yi"))
    if summary.get("southbound_available") is False:
        south = None
    south_available = south is not None

    return {
        "trade_date": normalized_anchor,
        "southbound_net_yi": round(south, 2) if south is not None else None,
        "southbound_available": south_available,
        "southbound_reason": None if south_available else (
            summary.get("southbound_reason") or "source_value_missing"
        ),
        # 解释始终由南向数值重建，不复用旧缓存文案。
        "interpretation": _build_interpretation(south),
        "source": summary.get("source") or "eastmoney_southbound_summary_via_akshare",
        "fetched_at": summary.get("fetched_at"),
        "stale": bool(summary.get("stale")),
    }


def _has_available_flow(summary: dict) -> bool:
    return bool(summary.get("southbound_available"))


def _stale_fallback_within_boundary(summary: dict) -> bool:
    fetched_at = summary.get("fetched_at")
    if not fetched_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_seconds = (_utc_now() - parsed.astimezone(timezone.utc)).total_seconds()
    return 0 <= age_seconds <= _STALE_FALLBACK_MAX_AGE_SECONDS


def _resolve_anchor(trade_date: str | None) -> str:
    requested = _normalize_trade_date(trade_date)
    if requested is not None:
        return requested
    session = build_trading_session()
    effective = _normalize_trade_date(session.get("effective_trade_date"))
    return effective or date.today().isoformat()


def _normalize_trade_date(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("/", "-").replace(".", "-")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _coerce_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("亿", "").strip())
    except ValueError:
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _build_interpretation(southbound_net_yi: float | None) -> str:
    if southbound_net_yi is None:
        return "南向资金数据暂不可用，不参与当前判断。"
    if southbound_net_yi > 0:
        detail = f"南向净流入约 {southbound_net_yi:.0f} 亿，可作为港股资金面的独立参考。"
    elif southbound_net_yi < 0:
        detail = f"南向净流出约 {abs(southbound_net_yi):.0f} 亿，可作为港股资金面的独立参考。"
    else:
        detail = "南向净流量约 0 亿，可作为港股资金面的独立参考。"
    return detail


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
