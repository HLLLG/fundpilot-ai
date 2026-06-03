from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from typing import Any

from app.services.sector_canonical import fetch_canonical_sector_quote
from app.services.eastmoney_spot_client import fetch_eastmoney_sector_quote
from app.services.sector_labels import normalize_sector_label
from app.services.sector_quote_provider import SpotBoard
from app.services.sector_quote_resolver import SectorResolveResult

logger = logging.getLogger(__name__)

_ON_DEMAND_SCRIPT = """
import json
import os
import sys

for key in list(os.environ):
    if "proxy" in key.lower():
        os.environ.pop(key)
os.environ["NO_PROXY"] = "*"

sector_name = sys.argv[1]
source_type = sys.argv[2]

try:
    import akshare as ak
except Exception as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))
    raise SystemExit(0)

def pick_change(frame, change_col):
    if frame is None or getattr(frame, "empty", True):
        return None
    if change_col in frame.columns:
        value = frame.iloc[0][change_col]
    elif "item" in frame.columns and "value" in frame.columns:
        row = frame.loc[frame["item"] == change_col]
        if row.empty:
            return None
        value = row.iloc[0]["value"]
    else:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None

try:
    if source_type == "concept":
        for fn, col in (
            (lambda: ak.stock_board_concept_spot_em(symbol=sector_name), "涨跌幅"),
            (lambda: ak.stock_board_concept_name_em(), "涨跌幅"),
        ):
            frame = fn()
            if col == "涨跌幅" and "板块名称" in getattr(frame, "columns", []):
                matched = frame.loc[frame["板块名称"] == sector_name]
                if not matched.empty:
                    change = round(float(matched.iloc[0]["涨跌幅"]), 4)
                    print(json.dumps({
                        "ok": True,
                        "change_percent": change,
                        "matched_name": sector_name,
                        "source_type": "concept",
                    }))
                    raise SystemExit(0)
            change = pick_change(frame, col)
            if change is not None:
                print(json.dumps({
                    "ok": True,
                    "change_percent": change,
                    "matched_name": sector_name,
                    "source_type": "concept",
                }))
                raise SystemExit(0)
    if source_type == "industry":
        for fn, col in (
            (lambda: ak.stock_board_industry_spot_em(symbol=sector_name), "涨跌幅"),
            (lambda: ak.stock_board_industry_name_em(), "涨跌幅"),
        ):
            frame = fn()
            if col == "涨跌幅" and "板块名称" in getattr(frame, "columns", []):
                matched = frame.loc[frame["板块名称"] == sector_name]
                if not matched.empty:
                    change = round(float(matched.iloc[0]["涨跌幅"]), 4)
                    print(json.dumps({
                        "ok": True,
                        "change_percent": change,
                        "matched_name": sector_name,
                        "source_type": "industry",
                    }))
                    raise SystemExit(0)
            change = pick_change(frame, col)
            if change is not None:
                print(json.dumps({
                    "ok": True,
                    "change_percent": change,
                    "matched_name": sector_name,
                    "source_type": "industry",
                }))
                raise SystemExit(0)
except Exception as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))

print(json.dumps({"ok": False, "error": "not found"}))
"""


def fetch_sector_on_demand(
    sector_name: str | None,
    boards: dict[str, SpotBoard],
) -> SectorResolveResult | None:
    """全量板块拉取失败/不全时，按板块名单独补拉（如商业航天）。"""
    label = normalize_sector_label(sector_name)
    if not label:
        return None

    concept_board = boards.get("concept") or {}
    if label in concept_board:
        return SectorResolveResult(
            confidence="high",
            change_percent=concept_board[label],
            matched_name=label,
            source_type="concept",
        )

    industry_board = boards.get("industry") or {}
    if label in industry_board:
        return SectorResolveResult(
            confidence="high",
            change_percent=industry_board[label],
            matched_name=label,
            source_type="industry",
        )

    canonical = fetch_canonical_sector_quote(sector_name, boards)
    if canonical is not None:
        return SectorResolveResult(
            confidence="high",
            change_percent=canonical.change_percent,
            matched_name=canonical.matched_name,
            source_type=canonical.source_type,
            source_code=canonical.source_code,
            message=canonical.message,
        )

    for source_type in ("concept", "industry"):
        change = fetch_eastmoney_sector_quote(label, source_type=source_type)
        if change is not None:
            boards.setdefault(source_type, {})[label] = change
            return SectorResolveResult(
                confidence="high",
                change_percent=change,
                matched_name=label,
                source_type=source_type,
            )

    for source_type in ("concept", "industry"):
        payload = _fetch_via_subprocess(label, source_type)
        if payload and payload.get("ok"):
            return SectorResolveResult(
                confidence="high",
                change_percent=float(payload["change_percent"]),
                matched_name=str(payload["matched_name"]),
                source_type=str(payload["source_type"]),
            )

    return None


def _fetch_via_subprocess(sector_name: str, source_type: str, *, attempts: int = 3) -> dict[str, Any] | None:
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            completed = subprocess.run(
                [sys.executable, "-c", _ON_DEMAND_SCRIPT, sector_name, source_type],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            stdout = (completed.stdout or "").strip()
            if not stdout:
                last_error = completed.stderr or "empty stdout"
                continue
            payload = json.loads(stdout.splitlines()[-1])
            if payload.get("ok"):
                return payload
            last_error = str(payload.get("error") or "on-demand miss")
        except Exception as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(0.6 * (attempt + 1))
    if last_error:
        logger.info("sector on-demand %s/%s failed: %s", sector_name, source_type, last_error)
    return None
