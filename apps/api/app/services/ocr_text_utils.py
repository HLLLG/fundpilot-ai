from __future__ import annotations

import re

PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
SIGNED_AMOUNT_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")
UNSIGNED_AMOUNT_RE = re.compile(r"^\d+(?:\.\d+)?$")
NEGATIVE_MARKER_RE = re.compile(r"^[-—－―+]$")
DAILY_PLACEHOLDER_RE = re.compile(r"^[-—－―.=]+$")


def extract_percent(line: str) -> float | None:
    match = PERCENT_RE.search(line)
    if not match:
        return None
    return float(match.group(1))


def is_negative_marker_line(line: str) -> bool:
    cleaned = line.strip()
    return bool(NEGATIVE_MARKER_RE.match(cleaned)) or bool(DAILY_PLACEHOLDER_RE.match(cleaned))


def parse_amount_token(line: str) -> float | None:
    cleaned = line.replace(",", "").strip()
    if SIGNED_AMOUNT_RE.match(cleaned):
        return float(cleaned)
    if UNSIGNED_AMOUNT_RE.match(cleaned):
        return float(cleaned)
    return None


def extract_signed_numbers(lines: list[str]) -> list[float]:
    values: list[float] = []
    pending_negative = False
    for line in lines:
        cleaned = line.replace(",", "").strip()
        if is_negative_marker_line(cleaned):
            pending_negative = True
            continue
        parsed = parse_amount_token(cleaned)
        if parsed is None:
            if not cleaned or cleaned in ("PK", "0", "="):
                continue
            pending_negative = False
            continue
        if pending_negative and parsed > 0:
            parsed = -parsed
        pending_negative = False
        values.append(parsed)
    return values


def extract_signed_percents(lines: list[str]) -> list[float]:
    values: list[float] = []
    pending_negative = False
    for line in lines:
        cleaned = line.strip()
        if is_negative_marker_line(cleaned):
            pending_negative = True
            continue
        matched = False
        for match in PERCENT_RE.finditer(cleaned):
            value = float(match.group(1))
            if pending_negative and value > 0:
                value = -value
            pending_negative = False
            values.append(value)
            matched = True
        if not matched and cleaned and parse_amount_token(cleaned.replace(",", "")) is None:
            pending_negative = False
    return values


def align_profit_sign(
    profit: float | None,
    return_percent: float | None,
) -> float | None:
    if profit is None or return_percent is None or profit == 0 or return_percent == 0:
        return profit
    if (profit > 0) > (return_percent > 0):
        return -abs(profit)
    return profit


def is_near_zero(value: float) -> bool:
    return abs(value) < 0.0001


def infer_holding_profit(
    *,
    holding_amount: float | None,
    holding_return_percent: float | None,
    holding_profit: float | None,
) -> float | None:
    aligned = align_profit_sign(holding_profit, holding_return_percent)
    if aligned is not None and not is_near_zero(aligned):
        return aligned
    if holding_amount is None or holding_return_percent is None:
        return aligned
    inferred = round(
        holding_amount * holding_return_percent / (100 + holding_return_percent),
        2,
    )
    return align_profit_sign(inferred, holding_return_percent)
