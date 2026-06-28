from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrimarySectorRecord:
    fund_code: str
    sector_name: str
    intraday_index_name: str | None
    source: str
    confidence: float | None = None
    detail: dict | None = None
