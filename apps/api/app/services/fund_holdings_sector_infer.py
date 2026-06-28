"""季报重仓穿透 → 主关联板块（东财个股行业加权，替代手工关键词表）。"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass

from app.services.fund_industry_theme_map import map_industry_to_theme_label
from app.services.sector_canonical import get_canonical_sector

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 90
_MIN_SCORE_PERCENT = 8.0
_MAX_STOCKS_WITH_INDUSTRY = 8


@dataclass(frozen=True)
class HoldingStockRow:
    name: str
    weight: float
    industry: str | None = None
    stock_code: str | None = None


def fetch_portfolio_stocks_with_industry(fund_code: str) -> list[HoldingStockRow]:
    """拉取最新季报前 N 重仓股及东财行业（子进程 AkShare）。"""
    code = fund_code.strip().zfill(6)
    if len(code) != 6:
        return []

    script = r"""
import json
import sys
from datetime import datetime

import akshare as ak

code = sys.argv[1]
max_stocks = int(sys.argv[2])
years = [str(datetime.now().year), str(datetime.now().year - 1)]
rows = []
for year in years:
    try:
        frame = ak.fund_portfolio_hold_em(symbol=code, date=year)
    except Exception:
        continue
    if frame is None or frame.empty:
        continue
    name_col = "股票名称" if "股票名称" in frame.columns else frame.columns[1]
    code_col = "股票代码" if "股票代码" in frame.columns else None
    weight_col = "占净值比例" if "占净值比例" in frame.columns else None
    if weight_col is None:
        for col in frame.columns:
            if "比例" in str(col) or "占比" in str(col):
                weight_col = col
                break
    if weight_col is None:
        continue
    for _, row in frame.head(max_stocks).iterrows():
        name = str(row[name_col]).strip()
        stock_code = ""
        if code_col is not None:
            stock_code = str(row[code_col]).strip().split(".")[-1].zfill(6)
        try:
            weight = float(row[weight_col])
        except Exception:
            weight = 0.0
        if not name or weight <= 0:
            continue
        industry = ""
        if stock_code and stock_code.isdigit() and len(stock_code) == 6:
            try:
                info = ak.stock_individual_info_em(symbol=stock_code)
                if info is not None and not info.empty:
                    for _, info_row in info.iterrows():
                        item = str(info_row.get("item", "")).strip()
                        if item in ("行业", "所属行业"):
                            value = info_row.get("value")
                            if value is not None:
                                industry = str(value).strip()
                            break
            except Exception:
                industry = ""
        rows.append(
            {
                "name": name,
                "weight": weight,
                "industry": industry or None,
                "stock_code": stock_code or None,
            }
        )
    if rows:
        break
print(json.dumps(rows, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, code, str(_MAX_STOCKS_WITH_INDUSTRY)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, list):
            return []
        rows: list[HoldingStockRow] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            rows.append(
                HoldingStockRow(
                    name=str(item.get("name", "")).strip(),
                    weight=float(item.get("weight", 0) or 0),
                    industry=(str(item["industry"]).strip() if item.get("industry") else None),
                    stock_code=(str(item["stock_code"]).strip() if item.get("stock_code") else None),
                )
            )
        return rows
    except Exception:
        logger.exception("portfolio industry fetch failed for %s", fund_code)
        return []


def infer_sector_from_portfolio_stocks(
    fund_code: str,
    stocks: list[HoldingStockRow],
) -> tuple[str, dict[str, float], list[dict]] | None:
    """按重仓行业加权投票，返回 (sector_name, scores, evidence)。"""
    scores: dict[str, float] = {}
    evidence: list[dict] = []
    for row in stocks:
        if row.weight <= 0:
            continue
        theme = map_industry_to_theme_label(row.industry)
        if not theme:
            continue
        scores[theme] = scores.get(theme, 0.0) + row.weight
        evidence.append(
            {
                "stock": row.name,
                "stock_code": row.stock_code,
                "weight": row.weight,
                "industry": row.industry,
                "theme": theme,
            }
        )

    if not scores:
        return None

    sector_name = max(scores, key=lambda key: scores[key])
    if scores[sector_name] < _MIN_SCORE_PERCENT:
        return None
    if not get_canonical_sector(sector_name):
        from app.services.sector_registry_data import THEME_BOARD_INDEX

        if sector_name not in THEME_BOARD_INDEX:
            return None
    return sector_name, scores, evidence
