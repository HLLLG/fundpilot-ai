"""基金诊断信息（类型/管理费/规模/1年收益）全用户共享缓存。"""

from __future__ import annotations

from app.services.akshare_subprocess import run_akshare_json_script
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import build_trading_session

_CACHE_VERSION = "v4"
_LIVE_TTL_SECONDS = 3600.0
_CLOSED_TTL_SECONDS = 86400.0
_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
    "trading_day_pre_open",
}


def _cache_ttl_seconds() -> float:
    session_kind = str(build_trading_session().get("session_kind") or "")
    if session_kind in _INTRADAY_SESSIONS:
        return _LIVE_TTL_SECONDS
    return _CLOSED_TTL_SECONDS


def diagnostics_cache_key(fund_code: str) -> str:
    return f"fund:diagnostics:{_CACHE_VERSION}:{fund_code}"


def get_cached_fund_diagnostics(fund_code: str) -> dict | None:
    payload = get_spot_snapshot(
        diagnostics_cache_key(fund_code),
        ttl_seconds=_cache_ttl_seconds(),
    )
    if isinstance(payload, dict) and payload:
        return payload
    return None


def save_cached_fund_diagnostics(fund_code: str, diagnostics: dict) -> None:
    if not diagnostics:
        return
    save_spot_snapshot(diagnostics_cache_key(fund_code), diagnostics)


def load_fund_diagnostics(fund_code: str) -> dict:
    """cache-aside：AkShare 基金概览 + 累计收益率。"""
    cached = get_cached_fund_diagnostics(fund_code)
    if cached is not None:
        return dict(cached)

    diagnostics = _fetch_fund_diagnostics_via_akshare(fund_code)
    if diagnostics:
        save_cached_fund_diagnostics(fund_code, diagnostics)
    return diagnostics


def _fetch_fund_diagnostics_via_akshare(fund_code: str) -> dict:
    from app.services.fund_data import _parse_overview_frame, _parse_return_frame

    script = f"""
import json
import sys

def _dump_frame(frame):
    if frame is None or frame.empty:
        return {{"columns": [], "rows": []}}
    rows = []
    for _, row in frame.iterrows():
        values = []
        for value in row.tolist():
            text = str(value)
            if text.lower() in ("nan", "nat"):
                values.append(None)
            else:
                values.append(value)
        rows.append(values)
    return {{
        "columns": [str(col) for col in frame.columns],
        "rows": rows,
    }}

try:
    import akshare as ak

    overview = ak.fund_overview_em(symbol={fund_code!r})
    cumulative = ak.fund_open_fund_info_em(symbol={fund_code!r}, indicator="累计收益率走势")
    print(json.dumps({{
        "overview": _dump_frame(overview),
        "cumulative": _dump_frame(cumulative),
    }}, ensure_ascii=False, default=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
    sys.exit(1)
"""
    payload = run_akshare_json_script(
        script,
        label=f"fund diagnostics {fund_code}",
        timeout=60,
    )
    if not isinstance(payload, dict):
        return {}
    diagnostics: dict = {}
    overview = _frame_from_payload(payload.get("overview"))
    if overview is not None:
        diagnostics.update(_parse_overview_frame(overview))
    cumulative = _frame_from_payload(payload.get("cumulative"))
    if cumulative is not None:
        diagnostics.update(_parse_return_frame(cumulative))
    return diagnostics


def _frame_from_payload(payload: object):
    if not isinstance(payload, dict):
        return None
    columns = payload.get("columns")
    rows = payload.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return None
    if not columns:
        return None
    try:
        import pandas as pd

        return pd.DataFrame(rows, columns=[str(col) for col in columns])
    except Exception:
        return None
