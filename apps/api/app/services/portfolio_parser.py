from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models import PortfolioSummary

NUMBER_RE = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?$")
PERCENT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)%$")
SUMMARY_MARKERS = ("账户汇总", "账户资产")


def parse_portfolio_summary_from_text(text: str) -> PortfolioSummary | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = _find_summary_start(lines)
    if start is None:
        return None

    numbers: list[float] = []
    percents: list[float] = []
    for line in lines[start + 1 : start + 8]:
        if any(marker in line for marker in ("当日收益", "关联板块", "持有收益")):
            break
        cleaned = line.replace(",", "").strip()
        percent_match = PERCENT_RE.match(cleaned)
        if percent_match:
            percents.append(float(percent_match.group(1)))
        elif NUMBER_RE.match(cleaned):
            numbers.append(float(cleaned))

    if not numbers:
        return None

    total_assets = numbers[0]
    daily_profit = numbers[1] if len(numbers) > 1 else None
    daily_return_percent = percents[0] if percents else None

    if daily_return_percent is None and daily_profit is not None and total_assets:
        daily_return_percent = round(daily_profit / total_assets * 100, 4)

    daily_profit_source = None
    if daily_profit is not None:
        daily_profit_source = (
            "penetration_estimate" if "场内穿透" in text else "settled"
        )

    return PortfolioSummary(
        total_assets=total_assets,
        daily_profit=daily_profit,
        daily_return_percent=daily_return_percent,
        daily_profit_source=daily_profit_source,
        updated_at=datetime.now(timezone.utc),
    )


def _find_summary_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if any(marker in line for marker in SUMMARY_MARKERS):
            return index
    return None
