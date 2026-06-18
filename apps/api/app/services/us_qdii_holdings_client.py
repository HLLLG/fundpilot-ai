"""QDII 基金季报重仓持仓（AkShare ``fund_portfolio_hold_em`` 子进程约定）。

用于 Phase 2「穿透估值」：按季报披露的前十大重仓股及占净值比例，
结合个股实时涨跌加权估算各基金参考涨跌。

持仓数据按基金代码缓存 24h（季报低频更新，避免每次 snapshot 触发 15 次子进程）。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from typing import Any

from app.services.sector_quote_cache import (
    get_spot_snapshot,
    save_spot_snapshot,
)

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 45
_HOLDINGS_TTL_SECONDS = 86400.0  # 24h
_CACHE_PREFIX = "market:us_qdii_holdings:v1"

_US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def classify_holding_market(code: str) -> str:
    """根据股票代码推断市场：us / hk / cn / unknown。"""
    raw = str(code or "").strip().upper()
    if not raw:
        return "unknown"
    if _US_TICKER_RE.match(raw):
        return "us"
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 5:
        return "hk"
    if len(digits) == 6:
        return "cn"
    return "unknown"


def normalize_holding_code(code: str, market: str) -> str:
    """统一 lookup 键：美股大写 ticker；港股 5 位；A 股 6 位。"""
    raw = str(code or "").strip().upper()
    if market == "us":
        return raw
    digits = re.sub(r"\D", "", raw)
    if market == "hk":
        return digits.zfill(5)[-5:]
    if market == "cn":
        return digits.zfill(6)[-6:]
    return raw


_HOLDINGS_SCRIPT = r"""
import json
import os
import sys
from datetime import datetime

for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
os.environ.pop("REQUESTS_CA_BUNDLE", None)
os.environ.pop("CURL_CA_BUNDLE", None)

code = sys.argv[1]
years = [str(datetime.now().year), str(datetime.now().year - 1)]
rows = []
report_date = None
for year in years:
    try:
        import akshare as ak
        frame = ak.fund_portfolio_hold_em(symbol=code, date=year)
    except Exception:
        continue
    if frame is None or frame.empty:
        continue
    code_col = "股票代码" if "股票代码" in frame.columns else None
    name_col = "股票名称" if "股票名称" in frame.columns else frame.columns[1]
    weight_col = "占净值比例" if "占净值比例" in frame.columns else None
    if code_col is None or weight_col is None:
        for col in frame.columns:
            if "代码" in str(col):
                code_col = col
            if "比例" in str(col) or "占比" in str(col):
                weight_col = col
    if code_col is None or weight_col is None:
        continue
    for _, row in frame.head(10).iterrows():
        stock_code = str(row[code_col]).strip()
        name = str(row[name_col]).strip()
        try:
            weight = float(row[weight_col])
        except Exception:
            weight = 0.0
        if stock_code and weight > 0:
            rows.append({"code": stock_code, "name": name, "weight": weight})
    if rows:
        if "季度" in frame.columns:
            report_date = str(frame.iloc[0]["季度"])
        break
print(json.dumps({"holdings": rows, "report_date": report_date}, ensure_ascii=False))
"""


def _cache_key(fund_code: str) -> str:
    return f"{_CACHE_PREFIX}:{fund_code}"


def _fetch_holdings_subprocess(fund_code: str) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _HOLDINGS_SCRIPT, fund_code],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, dict):
            return None
        holdings = payload.get("holdings")
        if not isinstance(holdings, list) or not holdings:
            return None
        normalized: list[dict[str, Any]] = []
        for row in holdings:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code", "")).strip()
            market = classify_holding_market(code)
            if market == "unknown":
                continue
            weight = row.get("weight")
            try:
                w = float(weight)
            except (TypeError, ValueError):
                continue
            if w <= 0:
                continue
            normalized.append(
                {
                    "code": normalize_holding_code(code, market),
                    "name": str(row.get("name", "")).strip(),
                    "weight": w,
                    "market": market,
                }
            )
        if not normalized:
            return None
        return {
            "fund_code": fund_code,
            "holdings": normalized,
            "report_date": payload.get("report_date"),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception:
        logger.exception("qdii holdings fetch failed for %s", fund_code)
        return None


def get_fund_holdings(
    fund_code: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    """读取单只基金重仓（缓存 24h）；失败返回 None。"""
    key = _cache_key(fund_code)
    if not force_refresh:
        cached = get_spot_snapshot(key, ttl_seconds=_HOLDINGS_TTL_SECONDS)
        if cached and cached.get("holdings"):
            return cached

    fresh = _fetch_holdings_subprocess(fund_code)
    if fresh:
        save_spot_snapshot(key, fresh)
        return fresh

    stale = get_spot_snapshot(key, ttl_seconds=10 * _HOLDINGS_TTL_SECONDS)
    return stale if stale and stale.get("holdings") else None


def load_qdii_holdings_batch(
    fund_codes: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """批量加载种子基金持仓；返回 fund_code → holdings payload。"""
    out: dict[str, dict[str, Any]] = {}
    for code in fund_codes:
        payload = get_fund_holdings(code, force_refresh=force_refresh)
        if payload:
            out[code] = payload
    return out
